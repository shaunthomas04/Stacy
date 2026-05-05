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
from database_functions import (
    upsert_user, initialize_guild, get_user_score,
    get_guild_policy, update_guild_policy,
    set_decay_interval, set_decay_amount, decay_scores_due,
    get_guild_sensitivity, set_guild_sensitivity,
    reset_user_score,
)
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
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ------------------
# Constants & Cache
# ------------------

DEFAULT_POLICY = (
    "Be respectful to all members. No harassment, hate speech, slurs, or targeted abuse. "
    "Keep discussions civil, avoid spamming, and do not share NSFW content outside designated channels. "
    "Repeated or severe violations will be escalated. Stacy is always watching."
)

# guild_id -> hr_policy string
_guild_cache: dict[str, str] = {}
# guild_id -> sensitivity string ('low', 'medium', 'high')
_sensitivity_cache: dict[str, str] = {}


def _ensure_guild(guild_id: str, guild_name: str) -> str:
    """
    Returns the guild's HR policy. On first call for a guild: writes the row to
    DB if it doesn't exist, fetches the stored policy and sensitivity, and caches both.
    Subsequent calls return the cached values with zero DB hits.
    """
    if guild_id not in _guild_cache:
        initialize_guild(guild_id, guild_name, DEFAULT_POLICY)
        _guild_cache[guild_id] = get_guild_policy(guild_id) or DEFAULT_POLICY
        _sensitivity_cache[guild_id] = get_guild_sensitivity(guild_id)
    return _guild_cache[guild_id]


# ------------------
# Cron Job
# ------------------

async def decay_and_sync_job():
    """
    Runs every minute. For each guild: applies score decay if it's due,
    then syncs Discord roles for any guild whose scores changed.
    """
    decayed_ids = await asyncio.to_thread(decay_scores_due)
    if decayed_ids:
        print(f"⏰ Decay applied to guild(s): {decayed_ids}")
    for guild in bot.guilds:
        if str(guild.id) in decayed_ids:
            try:
                await sync_all_roles(guild)
            except Exception as e:
                print(f"❌ Role sync after decay failed for {guild.name}: {e}")


# ------------------
# Helpers
# ------------------

async def run_stacy(
    user_id: str,
    guild_id: str,
    target_user_id: str,
    message_content: str,
    hr_policy: str = "",
    sensitivity: str = "low",
    username: str = "",
    target_username: str = "",
    participants: dict | None = None,
    image_b64: str = "",
    image_mime: str = "",
) -> str | None:
    """
    Runs the Stacy LangGraph agent and returns the final response string, or None if ignored.
    Wrapped in asyncio.to_thread since LangGraph is synchronous.
    """
    if image_b64:
        msg_content = [
            {"type": "text", "text": message_content or ""},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
        ]
    else:
        msg_content = message_content or "[no message]"

    inputs = {
        "messages": [HumanMessage(content=msg_content)],
        "user_id": user_id,
        "guild_id": guild_id,
        "target_user_id": target_user_id,
        "hr_policy": hr_policy,
        "sensitivity": sensitivity,
        "username": username,
        "target_username": target_username,
        "participants": participants or {},
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
    scheduler.add_job(decay_and_sync_job, "interval", minutes=1)
    scheduler.start()
    print("⏰ Scheduler started — decay + role sync check every 1 min")

    # Warm cache, ensure all guilds exist in DB, and sync roles on startup
    for guild in bot.guilds:
        _ensure_guild(str(guild.id), guild.name)
        await sync_all_roles(guild)


@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    upsert_user(str(member.id), guild_id, member.display_name)
    try:
        await setup_and_assign_hr_role(member.guild, member, 0)
    except Exception as e:
        print(f"❌ Role assignment failed for new member {member.display_name}: {e}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    hr_policy = _ensure_guild(guild_id, message.guild.name)

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
                message.content, hr_policy,
                _sensitivity_cache.get(guild_id, "low"),
                message.author.display_name, target_username,
                image_b64=image_b64, image_mime=image_mime,
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
        display_name = message.author.display_name

        response = await run_stacy(
            user_id=user_id,
            guild_id=guild_id,
            target_user_id=user_id,
            message_content=message.content or "[image]",
            hr_policy=hr_policy,
            sensitivity=_sensitivity_cache.get(guild_id, "low"),
            username=display_name,
            target_username=display_name,
            image_b64=image_b64,
            image_mime=image_mime,
        )

        if response:
            await message.reply(response[:1990])

            try:
                score = get_user_score(user_id, guild_id)
                member = message.guild.get_member(int(user_id))
                if member:
                    await setup_and_assign_hr_role(message.guild, member, score)
            except Exception as e:
                print(f"❌ Role update failed after infraction: {e}")


# ------------------
# Commands
# ------------------

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("🏓 Pong!")


@bot.command(name="hello")
async def hello(ctx):
    await ctx.send(f"Hello {ctx.author.mention}, HR acknowledges your presence. 👔")


@bot.command(name="policy")
async def policy(ctx):
    policy = get_guild_policy(str(ctx.guild.id)) or DEFAULT_POLICY
    await ctx.send(f"**📋 {ctx.guild.name} HR Policy:**\n{policy}")


@bot.command(name="stacyHelp")
async def stacy_help(ctx):
    await ctx.send(
        "⚖️  **STACY HR SYSTEM**  ⚖️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 **GENERAL**\n"
        "`!stacyHelp` — Show this menu\n"
        "`!policy` — View this server's HR policy\n"
        "`!history @user` — Pull up a user's HR report\n\n"
        "🔒 **SERVER OWNER ONLY**\n"
        "`!setPolicy <text>` — Update the server's HR rules\n"
        "`!setSensitivity <low|medium|high>` — Set how strictly Stacy enforces policy *(default: low)*\n"
        "`!setDecayInterval <minutes>` — How often scores decay *(default: 1440 min)*\n"
        "`!setDecayAmount <points>` — Points removed per decay tick *(default: 5)*\n"
        "`!pardon @user` — Clear a user's record and reset their score to 0\n\n"
        "👀 **PASSIVE MODERATION**\n"
        "Stacy reads every message and enforces server policy automatically.\n"
        "`@Stacy <question>` — Ask Stacy about HR rules\n"
        "`@Stacy @user <reason>` — Report a user to HR\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Stacy is always watching.* 👁️"
    )


@bot.command(name="setPolicy")
async def set_policy(ctx, *, policy: str = None):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only server owners can update policy.")
        return
    if not policy:
        await ctx.send("Usage: `!setPolicy <policy text>`")
        return
    guild_id = str(ctx.guild.id)
    update_guild_policy(guild_id, policy)
    _guild_cache[guild_id] = policy
    await ctx.send("Policy has been updated!")


@bot.command(name="setDecayInterval")
async def set_decay_interval_cmd(ctx, minutes: int = None):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only server owners can update decay settings.")
        return
    if minutes is None or minutes < 1:
        await ctx.send("Usage: `!setDecayInterval <minutes>` — e.g. `!setDecayInterval 1440` for 24 hours.")
        return
    set_decay_interval(str(ctx.guild.id), minutes)
    await ctx.send(f"Decay interval updated — scores will decay every {minutes} minute(s).")


@bot.command(name="setDecayAmount")
async def set_decay_amount_cmd(ctx, amount: int = None):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only server owners can update decay settings.")
        return
    if amount is None or amount < 1:
        await ctx.send("Usage: `!setDecayAmount <points>` — e.g. `!setDecayAmount 5`.")
        return
    set_decay_amount(str(ctx.guild.id), amount)
    await ctx.send(f"Decay amount updated — {amount} point(s) will be removed per decay tick.")


@bot.command(name="setSensitivity")
async def set_sensitivity_cmd(ctx, level: str = None):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only server owners can update sensitivity settings.")
        return
    if level not in ("low", "medium", "high"):
        await ctx.send("Usage: `!setSensitivity <low|medium|high>`")
        return
    guild_id = str(ctx.guild.id)
    set_guild_sensitivity(guild_id, level)
    _sensitivity_cache[guild_id] = level
    await ctx.send(f"Sensitivity updated to **{level}**. Stacy will now enforce policy at the **{level}** threshold.")


@bot.command(name="pardon")
async def pardon(ctx, member: discord.Member = None):
    if ctx.author.id != ctx.guild.owner_id:
        await ctx.send("Only server owners can issue pardons.")
        return
    if not member:
        await ctx.send("Usage: `!pardon @user`")
        return
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)
    reset_user_score(user_id, guild_id)
    try:
        await setup_and_assign_hr_role(ctx.guild, member, 0)
    except Exception as e:
        print(f"❌ Role reset failed for {member.display_name}: {e}")
    await ctx.send(
        f"I've been asked to process a full pardon for {member.display_name}. "
        f"Their record has been cleared and their standing reset to HR Approved. "
        f"I do want to note — just for the record — that I had some reservations about this, "
        f"but it's not my call. Fresh start, I guess."
    )


@bot.command(name="history")
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
    await ctx.send(f"[{target.display_name}'s HR History]({url})")


# ------------------
# Run
# ------------------

bot.run(TOKEN)