import os
import asyncio
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands
from langchain_core.messages import HumanMessage

# Import the compiled graph from your agent file
from stacy_graph import app
from database_functions import upsert_user, initialize_guild
from misc import generate_hr_report

# Load token from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO)

# Intents (required to read messages)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ------------------
# Helpers
# ------------------

async def run_stacy(user_id: str, guild_id: str, target_user_id: str, message_content: str) -> str | None:
    """
    Runs the Stacy LangGraph agent and returns the final response string, or None if ignored.
    Wrapped in asyncio.to_thread since LangGraph is synchronous.
    """
    inputs = {
        "messages": [HumanMessage(content=message_content)],
        "user_id": user_id,
        "guild_id": guild_id,
        "target_user_id": target_user_id,
    }

    def _run():
        for output in app.stream(inputs):
            for node_name, data in output.items():
                if "messages" in data:
                    content = data["messages"][-1].content
                    if content != "[Stacy has no response for this message]":
                        return content
        return None

    return await asyncio.to_thread(_run)


# ------------------
# Events
# ------------------

@bot.event
async def on_ready():
    print(f"✅ Stacy is online as {bot.user}")
    print(f"   Monitoring {len(bot.guilds)} server(s)")


@bot.event
async def on_message(message):
    # Never respond to bots (including herself)
    if message.author.bot:
        return

    # Always process commands first (!, etc.)
    await bot.process_commands(message)

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    # Auto-register the guild if it does not exist yet
    hr_policy = "Welcome to the server. We expect all members to treat each other with basic respect at all times. Harassment, hate speech, slurs, and targeted abuse of any kind will result in immediate action. Keep discussions civil, avoid spamming, and do not share inappropriate or NSFW content outside of designated channels. Repeated or severe violations will be escalated and may result in removal from the server. Stacy is always watching."
    initialize_guild(guild_id, message.guild.name, hr_policy)

    is_stacy_mentioned = bot.user in message.mentions

    if is_stacy_mentioned:
        # --- Flow 1: Manual report or HR question via @Stacy ---
        # Filter out Stacy herself from the mention list
        other_mentions = [m for m in message.mentions if m != bot.user]

        if other_mentions:
            # @Stacy @BadActor42 this guy used a slur
            target = other_mentions[0]
            target_user_id = str(target.id)
            target_username = target.display_name
        else:
            # @Stacy what's the leave policy?
            target_user_id = user_id
            target_username = message.author.display_name

        # Ensure both users exist in DB before graph runs
        upsert_user(user_id, guild_id, message.author.display_name)
        if target_user_id != user_id:
            upsert_user(target_user_id, guild_id, target_username)

        async with message.channel.typing():
            response = await run_stacy(user_id, guild_id, target_user_id, message.content)

        if response:
            await message.reply(response[:1990])  # Discord 2000 char limit

    else:
        # --- Flow 2: Passive monitoring — Stacy watches everyone ---
        # Ensure the author exists in DB
        upsert_user(user_id, guild_id, message.author.display_name)

        # Run silently — only acts if router flags a violation
        response = await run_stacy(user_id, guild_id, user_id, message.content)

        if response:
            await message.reply(response[:1990])


# ------------------
# Commands
# ------------------

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def hello(ctx):
    await ctx.send(f"Hello {ctx.author.mention}, HR acknowledges your presence. 👔")

@bot.command()
async def rules(ctx):
    await ctx.send(
        "**📋 HR Guidelines:**\n"
        "1. Be respectful to all members\n"
        "2. No harassment or hate speech\n"
        "3. All memes must be HR-approved\n\n"
        "To report a user: `@Stacy @offender your reason here`"
    )

@bot.command()
async def helpme(ctx):
    await ctx.send(
        "**🤖 Stacy Commands:**\n"
        "`!ping` – Test the bot\n"
        "`!hello` – Greet HR\n"
        "`!rules` – Read company rules\n"
        "`!helpme` – List commands\n\n"
        "**Reporting a user:**\n"
        "Mention `@Stacy` and then `@username` in the same message.\n"
        "Example: `@Stacy @BadActor42 just used a slur in general`"
    )

@bot.command(name="History")
async def hr_report(ctx, member: discord.Member = None):
    """
    Usage: !History @user
    Generates and sends an HTML HR report for the mentioned user.
    """
    target = member or ctx.author  # defaults to self if no mention
    guild_id = str(ctx.guild.id)
    user_id = str(target.id)

    async with ctx.typing():
        filepath = await asyncio.to_thread(generate_hr_report, user_id, guild_id)

    if not filepath:
        await ctx.send(f"❌ No record found for {target.display_name} in this server.")
        return

    await ctx.send(
        f"📋 **HR Report for @{target.display_name}**",
        file=discord.File(filepath, filename=f"HR_Report_{target.display_name}.html")
    )

# ------------------
# Run
# ------------------

bot.run(TOKEN)