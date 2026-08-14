"""
Core moderation logic: per-guild policy/sensitivity caching, running the
Stacy LangGraph agent, forum-thread escalation, and score decay.

Functions here take the Discord objects they need (guild, bot user, ...)
as arguments rather than importing the bot instance, so this module has
no dependency on bot.py and can't create an import cycle.
"""
import asyncio
import logging
from collections import deque

import discord
from langchain_core.messages import HumanMessage

import config
from database_functions import decay_scores_due, get_guild_policy, get_guild_sensitivity, get_user_score, initialize_guild
from social_credit import setup_and_assign_hr_role, sync_all_roles
from stacy_graph import app, get_forum_response

logger = logging.getLogger(__name__)

# guild_id -> hr_policy string
_guild_cache: dict[str, str] = {}
# guild_id -> sensitivity string ('low', 'medium', 'high')
_sensitivity_cache: dict[str, str] = {}
# thread_id -> deque of (author_name, content) for forum thread context
thread_buffers: dict[int, deque] = {}
# thread_id -> messages since Stacy last responded
thread_message_counts: dict[int, int] = {}


def ensure_guild(guild_id: str, guild_name: str) -> str:
    """
    Returns the guild's HR policy. On first call for a guild: writes the row to
    DB if it doesn't exist, fetches the stored policy and sensitivity, and caches both.
    Subsequent calls return the cached values with zero DB hits.
    """
    if guild_id not in _guild_cache:
        initialize_guild(guild_id, guild_name, config.DEFAULT_POLICY)
        _guild_cache[guild_id] = get_guild_policy(guild_id) or config.DEFAULT_POLICY
        _sensitivity_cache[guild_id] = get_guild_sensitivity(guild_id)
    return _guild_cache[guild_id]


def get_cached_sensitivity(guild_id: str) -> str:
    return _sensitivity_cache.get(guild_id, "low")


def set_cached_policy(guild_id: str, policy: str):
    _guild_cache[guild_id] = policy


def set_cached_sensitivity(guild_id: str, sensitivity: str):
    _sensitivity_cache[guild_id] = sensitivity


async def decay_and_sync_job(guilds: list[discord.Guild]):
    """
    Runs on a schedule. For each guild: applies score decay if it's due,
    then syncs Discord roles for any guild whose scores changed.
    """
    decayed_ids = await asyncio.to_thread(decay_scores_due)
    if decayed_ids:
        logger.info(f"Decay applied to guild(s): {decayed_ids}")
    for guild in guilds:
        if str(guild.id) in decayed_ids:
            try:
                await sync_all_roles(guild)
            except Exception as e:
                logger.error(f"Role sync after decay failed for {guild.name}: {e}")


async def get_violations_forum(guild: discord.Guild) -> discord.ForumChannel:
    existing = discord.utils.get(guild.channels, name=config.VIOLATIONS_FORUM_NAME)
    if existing and isinstance(existing, discord.ForumChannel):
        return existing
    return await guild.create_forum(
        name=config.VIOLATIONS_FORUM_NAME,
        topic="Severe HR policy violations escalated by Stacy."
    )


async def post_violation_thread(
    guild: discord.Guild, target_name: str, pts: int, violation_msg: str
):
    try:
        forum = await get_violations_forum(guild)
        content = (
            f"**Incident Report — {target_name}**\n\n"
            f"A severe policy violation has been escalated for formal review.\n\n"
            f"**Points Added:** {pts}\n"
            f"**Violation:**\n> {violation_msg[:800]}"
        )
        result = await forum.create_thread(name=f"Incident — {target_name}", content=content)
        thread_id = result.thread.id
        thread_buffers[thread_id] = deque(maxlen=20)
        thread_buffers[thread_id].append((
            "[Incident Report]",
            f"Violation by {target_name} ({pts} points): {violation_msg[:500]}"
        ))
    except Exception as e:
        logger.error(f"Failed to create violation thread: {e}")


def is_violations_thread(channel) -> bool:
    parent = getattr(channel, "parent", None)
    return isinstance(channel, discord.Thread) and isinstance(parent, discord.ForumChannel) \
        and parent.name == config.VIOLATIONS_FORUM_NAME


def _forum_interval(thread_id: int) -> int:
    """Returns how many messages Stacy waits before checking in unprompted.
    Scales with unique participant count so she backs off as more people join the conversation.
    Internal buffer entries (bracketed names like [Incident Report]) are excluded from the count."""
    if thread_id not in thread_buffers:
        return 1
    unique = len({name for name, _ in thread_buffers[thread_id] if not name.startswith("[")})
    return max(1, (unique - 1) * 3)


async def handle_forum_message(message: discord.Message, stacy_user: discord.ClientUser):
    thread_id = message.channel.id

    if thread_id not in thread_buffers:
        thread_buffers[thread_id] = deque(maxlen=20)
    thread_buffers[thread_id].append((message.author.display_name, message.content or "[image]"))

    directly_addressed = (
        stacy_user in message.mentions
        or "stacy" in message.content.lower()
    )

    if not directly_addressed:
        count = thread_message_counts.get(thread_id, 0) + 1
        thread_message_counts[thread_id] = count
        if count < _forum_interval(thread_id):
            return
        thread_message_counts[thread_id] = 0

    context = "\n".join(f"{name}: {msg}" for name, msg in thread_buffers[thread_id])

    try:
        async with message.channel.typing():
            response = await asyncio.to_thread(
                get_forum_response, message.channel.name, context, directly_addressed
            )
        if response:
            await message.reply(response[:1990])
    except discord.errors.NotFound:
        # Thread was deleted (e.g. via !resolve) before the response could be sent
        clear_thread(thread_id)


def clear_thread(thread_id: int):
    thread_buffers.pop(thread_id, None)
    thread_message_counts.pop(thread_id, None)


async def refresh_role(guild: discord.Guild, user_id: str):
    """Re-fetches a user's current score and updates their Discord role to match.
    Safe to call after any score change (infraction, appeal, pardon, decay)."""
    try:
        score = get_user_score(user_id, str(guild.id))
        member = guild.get_member(int(user_id))
        if member:
            await setup_and_assign_hr_role(guild, member, score)
    except Exception as e:
        logger.error(f"Role update failed for user {user_id}: {e}")


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
) -> tuple[str | None, str | None, int]:
    """
    Runs the Stacy LangGraph agent. Returns (response_text, severity, points).
    response_text is None if Stacy has nothing to say.
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
        response_text = None
        severity = None
        points = 0
        for output in app.stream(inputs):
            for node_name, data in output.items():
                if "severity" in data:
                    severity = data.get("severity")
                    points = int(data.get("points", 0))
                if "messages" in data:
                    content = data["messages"][-1].content
                    if content != "[Stacy has no response for this message]":
                        response_text = content
        return response_text, severity, points

    return await asyncio.to_thread(_run)


async def deliver_result(
    message: discord.Message,
    response: str | None,
    severity: str | None,
    pts: int,
    target_user_id: str,
    target_name: str,
):
    """Sends Stacy's reply, escalates to the violations forum if severe, and refreshes the target's role.
    Shared by both the @mention and passive on_message paths — they differ only in target resolution."""
    if not response:
        return
    await message.reply(response[:1990])
    if severity == "severe":
        await post_violation_thread(message.guild, target_name, pts, message.content or "[image]")
    await refresh_role(message.guild, target_user_id)
