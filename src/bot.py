"""
Stacy — Discord HR bot. Entrypoint: wires up the Discord client, its
event handlers, and the report server, then runs the bot.

Business logic lives in moderation.py (the moderation pipeline) and
commands.py (!prefix commands) — this file just connects them to
discord.py's event loop.
"""
import base64
import logging

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import moderation
from commands import register_commands
from database_functions import upsert_user
from report_server import start_report_server
from social_credit import setup_and_assign_hr_role, sync_all_roles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    logger.info(f"Stacy is online as {bot.user}")
    logger.info(f"Monitoring {len(bot.guilds)} server(s)")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: moderation.decay_and_sync_job(bot.guilds), "interval", minutes=1)
    scheduler.start()
    logger.info("Scheduler started — decay + role sync check every 1 min")

    # Warm cache, ensure all guilds exist in DB, and sync roles on startup
    for guild in bot.guilds:
        moderation.ensure_guild(str(guild.id), guild.name)
        await sync_all_roles(guild)


@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    upsert_user(str(member.id), guild_id, member.display_name)
    try:
        await setup_and_assign_hr_role(member.guild, member, 0)
    except Exception as e:
        logger.error(f"Role assignment failed for new member {member.display_name}: {e}")


async def _read_image_attachment(message: discord.Message) -> tuple[str, str]:
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            image_bytes = await attachment.read()
            return base64.b64encode(image_bytes).decode("utf-8"), attachment.content_type
    return "", ""


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    ctx = await bot.get_context(message)
    if ctx.valid:
        return

    # Forum thread: messages in policy-violations threads get dedicated context-aware handling
    if moderation.is_violations_thread(message.channel):
        await moderation.handle_forum_message(message, bot.user)
        return

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    hr_policy = moderation.ensure_guild(guild_id, message.guild.name)
    sensitivity = moderation.get_cached_sensitivity(guild_id)
    image_b64, image_mime = await _read_image_attachment(message)

    is_stacy_mentioned = bot.user in message.mentions

    if is_stacy_mentioned:
        other_mentions = [m for m in message.mentions if m != bot.user]
        if other_mentions:
            target = other_mentions[0]
            target_user_id, target_username = str(target.id), target.display_name
        else:
            target_user_id, target_username = user_id, message.author.display_name

        upsert_user(user_id, guild_id, message.author.display_name)
        if target_user_id != user_id:
            upsert_user(target_user_id, guild_id, target_username)

        async with message.channel.typing():
            response, severity, pts = await moderation.run_stacy(
                user_id, guild_id, target_user_id,
                message.content, hr_policy, sensitivity,
                message.author.display_name, target_username,
                image_b64=image_b64, image_mime=image_mime,
            )

        await moderation.deliver_result(message, response, severity, pts, target_user_id, target_username)

    else:
        upsert_user(user_id, guild_id, message.author.display_name)
        display_name = message.author.display_name

        response, severity, pts = await moderation.run_stacy(
            user_id=user_id,
            guild_id=guild_id,
            target_user_id=user_id,
            message_content=message.content or "[image]",
            hr_policy=hr_policy,
            sensitivity=sensitivity,
            username=display_name,
            target_username=display_name,
            image_b64=image_b64,
            image_mime=image_mime,
        )

        await moderation.deliver_result(message, response, severity, pts, user_id, display_name)


if __name__ == "__main__":
    public_url = start_report_server()
    register_commands(bot, public_url)
    bot.run(config.DISCORD_TOKEN)
