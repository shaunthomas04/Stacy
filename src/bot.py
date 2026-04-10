import os
import asyncio
import logging
import threading
import base64
from dotenv import load_dotenv
import discord
from discord.ext import commands
from langchain_core.messages import HumanMessage
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pyngrok import ngrok
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Import the compiled graph from your agent file
from stacy_graph import app
from database_functions import upsert_user, initialize_guild, get_user_score
from report import generate_hr_report
from social_credit import sync_all_roles, setup_and_assign_hr_role

# Load token from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ngrok.set_auth_token(os.getenv("NGROK_AUTHTOKEN"))

logging.basicConfig(level=logging.INFO)

# ------------------
# Reports Directory
# ------------------

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "temp")
os.makedirs(REPORTS_DIR, exist_ok=True)


# ------------------
# FastAPI Server
# ------------------

api = FastAPI()

@api.get("/report/{guild_id}/{user_id}")
async def serve_report(guild_id: str, user_id: str):
    filepath = os.path.join(REPORTS_DIR, f"hr_report_{user_id}_{guild_id}.html")
    if not os.path.exists(filepath):
        return HTMLResponse(
            "<h1 style='font-family:monospace;color:#e11d48'>Report not found.</h1>",
            status_code=404
        )
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={"ngrok-skip-browser-warning": "true"}
    )

def start_api():
    uvicorn.run(api, host="0.0.0.0", port=5000, log_level="warning")

# Start FastAPI in background thread and open ngrok tunnel
threading.Thread(target=start_api, daemon=True).start()
PUBLIC_URL = ngrok.connect(5000).public_url
print(f"\n✅ Report server live at: {PUBLIC_URL}\n")


# ------------------
# Discord Bot
# ------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ------------------
# Cron Job
# ------------------

async def role_sync_job():
    """Runs every 5 minutes — syncs HR roles for all members in all guilds."""
    print("⏰ Cron: Starting scheduled role sync...")
    for guild in bot.guilds:
        try:
            await sync_all_roles(guild)
        except Exception as e:
            print(f"❌ Cron error for guild {guild.name}: {e}")
    print("⏰ Cron: Role sync complete.")


# ------------------
# Helpers
# ------------------

async def run_stacy(
    user_id: str,
    guild_id: str,
    target_user_id: str,
    message_content: str,
    image_b64: str = "",
    image_mime: str = "",
) -> str | None:
    """
    Runs the Stacy LangGraph agent and returns the final response string, or None if ignored.
    Wrapped in asyncio.to_thread since LangGraph is synchronous.
    """
    inputs = {
        "messages": [HumanMessage(content=message_content or "[image only]")],
        "user_id": user_id,
        "guild_id": guild_id,
        "target_user_id": target_user_id,
        "image_b64": image_b64,
        "image_mime": image_mime,
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

    # Start the scheduler once the bot is ready and the event loop is running
    scheduler = AsyncIOScheduler()
    scheduler.add_job(role_sync_job, "interval", minutes=5)
    scheduler.start()
    print("⏰ Role sync scheduler started — running every 5 minutes")

    # Run an immediate sync on startup so roles are correct right away
    await role_sync_job()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    hr_policy = (
        "Welcome to the server. We expect all members to treat each other with basic respect at all times. "
        "Harassment, hate speech, slurs, and targeted abuse of any kind will result in immediate action. "
        "Keep discussions civil, avoid spamming, and do not share inappropriate or NSFW content outside of "
        "designated channels. Repeated or severe violations will be escalated and may result in removal from "
        "the server. Stacy is always watching. 👀"
    )
    initialize_guild(guild_id, message.guild.name, hr_policy)

    # Check for image attachments
    image_b64 = ""
    image_mime = ""
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            image_bytes = await attachment.read()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            image_mime = attachment.content_type
            break

    is_stacy_mentioned = bot.user in message.mentions

    if is_stacy_mentioned:
        other_mentions = [m for m in message.mentions if m != bot.user]

        if other_mentions:
            target = other_mentions[0]
            target_user_id = str(target.id)
            target_username = target.display_name
        else:
            target_user_id = user_id
            target_username = message.author.display_name

        upsert_user(user_id, guild_id, message.author.display_name)
        if target_user_id != user_id:
            upsert_user(target_user_id, guild_id, target_username)

        async with message.channel.typing():
            response = await run_stacy(
                user_id, guild_id, target_user_id,
                message.content, image_b64, image_mime
            )

        if response:
            await message.reply(response[:1990])

            # Immediately update the target's role after an infraction
            try:
                score = get_user_score(target_user_id, guild_id)
                target_member = message.guild.get_member(int(target_user_id))
                if target_member:
                    await setup_and_assign_hr_role(message.guild, target_member, score)
            except Exception as e:
                print(f"❌ Role update failed after infraction: {e}")

    else:
        upsert_user(user_id, guild_id, message.author.display_name)

        response = await run_stacy(
            user_id, guild_id, user_id,
            message.content, image_b64, image_mime
        )

        if response:
            await message.reply(response[:1990])

            # Immediately update the sender's role after a self-violation
            try:
                score = get_user_score(user_id, guild_id)
                await setup_and_assign_hr_role(message.guild, message.author, score)
            except Exception as e:
                print(f"❌ Role update failed after self-violation: {e}")


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
        "`!helpme` – List commands\n"
        "`!History @user` – View a user's HR report\n\n"
        "**Reporting a user:**\n"
        "Mention `@Stacy` and then `@username` in the same message.\n"
        "Example: `@Stacy @BadActor42 just used a slur in general`"
    )


@bot.command(name="History")
async def hr_report(ctx, member: discord.Member = None):
    """
    Usage: !History @user
    Generates an HTML HR report and sends a clickable hyperlink.
    """
    target = member or ctx.author
    guild_id = str(ctx.guild.id)
    user_id = str(target.id)

    async with ctx.typing():
        filepath = await asyncio.to_thread(generate_hr_report, user_id, guild_id, REPORTS_DIR)

    if not filepath:
        await ctx.send(f"❌ No record found for {target.display_name} in this server.")
        return

    url = f"{PUBLIC_URL}/report/{guild_id}/{user_id}"
    await ctx.send(f"[📋 {target.display_name}'s HR History]({url})")


# ------------------
# Run
# ------------------

bot.run(TOKEN)