"""
All !prefix commands. register_commands(bot, public_url) wires them onto
the bot instance — kept separate from bot.py so the entrypoint file stays
readable.
"""
import asyncio
import logging

import discord
from discord.ext import commands

import config
import moderation
from database_functions import (
    deduct_user_score, get_guild_policy, get_latest_infraction,
    get_user_score, mark_infraction_appealed, reset_user_score,
    set_decay_amount, set_decay_interval, set_guild_sensitivity,
    update_guild_policy,
)
from report import generate_hr_report
from report_server import REPORTS_DIR
from social_credit import setup_and_assign_hr_role
from stacy_graph import get_appeal_decision, get_pardon_response, get_resolve_response

logger = logging.getLogger(__name__)


def _is_owner(ctx) -> bool:
    return ctx.author.id == ctx.guild.owner_id


def register_commands(bot: commands.Bot, public_url: str):

    @bot.command(name="ping")
    async def ping(ctx):
        await ctx.send("Pong!")

    @bot.command(name="hello")
    async def hello(ctx):
        await ctx.send(f"Hello {ctx.author.mention}, HR acknowledges your presence.")

    @bot.command(name="policy")
    async def policy(ctx):
        guild_policy = get_guild_policy(str(ctx.guild.id)) or config.DEFAULT_POLICY
        await ctx.send(f"**{ctx.guild.name} HR Policy:**\n{guild_policy}")

    @bot.command(name="stacyHelp")
    async def stacy_help(ctx):
        await ctx.send(
            "**STACY HR SYSTEM**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**GENERAL**\n"
            "`!stacyHelp` — Show this menu\n"
            "`!policy` — View this server's HR policy\n"
            "`!history @user` — Pull up a user's HR report\n"
            "`!appeal <reason>` — Appeal your most recent infraction\n\n"
            "**SERVER OWNER ONLY**\n"
            "`!setPolicy <text>` — Update the server's HR rules\n"
            "`!setSensitivity <low|medium|high>` — Set how strictly Stacy enforces policy *(default: low)*\n"
            "`!setDecayInterval <minutes>` — How often scores decay *(default: 1440 min)*\n"
            "`!setDecayAmount <points>` — Points removed per decay tick *(default: 5)*\n"
            "`!pardon @user` — Clear a user's record and reset their score to 0\n"
            "`!resolve` — Post a closing note and delete the current policy-violations thread\n\n"
            "**PASSIVE MODERATION**\n"
            "Stacy reads every message and enforces server policy automatically.\n"
            "`@Stacy <question>` — Ask Stacy about HR rules\n"
            "`@Stacy @user <reason>` — Report a user to HR\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*Stacy is always watching.*"
        )

    @bot.command(name="appeal")
    async def appeal(ctx, *, reason: str = None):
        if not reason:
            await ctx.send("Usage: `!appeal <your reason>`")
            return

        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)

        infraction = get_latest_infraction(user_id, guild_id)
        if not infraction:
            await ctx.send("You don't have any unappealed infractions on record.")
            return

        async with ctx.typing():
            result = await asyncio.to_thread(
                get_appeal_decision,
                ctx.author.display_name,
                infraction["violation_context"],
                infraction["severity_level"],
                infraction["score_penalty"],
                reason,
            )

        mark_infraction_appealed(infraction["infraction_id"])

        if result["points_removed"] > 0:
            deduct_user_score(user_id, guild_id, result["points_removed"])
            await moderation.refresh_role(ctx.guild, user_id)

        await ctx.reply(result["message"])

    @bot.command(name="setPolicy")
    async def set_policy(ctx, *, policy_text: str = None):
        if not _is_owner(ctx):
            await ctx.send("Only server owners can update policy.")
            return
        if not policy_text:
            await ctx.send("Usage: `!setPolicy <policy text>`")
            return
        guild_id = str(ctx.guild.id)
        update_guild_policy(guild_id, policy_text)
        moderation.set_cached_policy(guild_id, policy_text)
        await ctx.send("Policy has been updated!")

    @bot.command(name="setDecayInterval")
    async def set_decay_interval_cmd(ctx, minutes: int = None):
        if not _is_owner(ctx):
            await ctx.send("Only server owners can update decay settings.")
            return
        if minutes is None or minutes < 1:
            await ctx.send("Usage: `!setDecayInterval <minutes>` — e.g. `!setDecayInterval 1440` for 24 hours.")
            return
        set_decay_interval(str(ctx.guild.id), minutes)
        await ctx.send(f"Decay interval updated — scores will decay every {minutes} minute(s).")

    @bot.command(name="setDecayAmount")
    async def set_decay_amount_cmd(ctx, amount: int = None):
        if not _is_owner(ctx):
            await ctx.send("Only server owners can update decay settings.")
            return
        if amount is None or amount < 1:
            await ctx.send("Usage: `!setDecayAmount <points>` — e.g. `!setDecayAmount 5`.")
            return
        set_decay_amount(str(ctx.guild.id), amount)
        await ctx.send(f"Decay amount updated — {amount} point(s) will be removed per decay tick.")

    @bot.command(name="setSensitivity")
    async def set_sensitivity_cmd(ctx, level: str = None):
        if not _is_owner(ctx):
            await ctx.send("Only server owners can update sensitivity settings.")
            return
        if level not in ("low", "medium", "high"):
            await ctx.send("Usage: `!setSensitivity <low|medium|high>`")
            return
        guild_id = str(ctx.guild.id)
        set_guild_sensitivity(guild_id, level)
        moderation.set_cached_sensitivity(guild_id, level)
        await ctx.send(f"Sensitivity updated to **{level}**. Stacy will now enforce policy at the **{level}** threshold.")

    @bot.command(name="pardon")
    async def pardon(ctx, member: discord.Member = None):
        if not _is_owner(ctx):
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
            logger.error(f"Role reset failed for {member.display_name}: {e}")
        async with ctx.typing():
            response = await asyncio.to_thread(get_pardon_response, member.display_name)
        await ctx.send(response)

    @bot.command(name="resolve")
    async def resolve_thread(ctx):
        if not _is_owner(ctx):
            await ctx.send("Only server owners can resolve HR threads.")
            return
        if not moderation.is_violations_thread(ctx.channel):
            await ctx.send("This command can only be used inside a policy-violations forum thread.")
            return

        async with ctx.typing():
            response = await asyncio.to_thread(get_resolve_response, ctx.channel.name)
        await ctx.send(response)

        moderation.clear_thread(ctx.channel.id)
        await ctx.channel.delete()

    @bot.command(name="history")
    async def hr_report(ctx, member: discord.Member = None):
        """Usage: !history @user — generates an HTML HR report and sends a clickable hyperlink."""
        target = member or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        async with ctx.typing():
            filepath = await asyncio.to_thread(generate_hr_report, user_id, guild_id, REPORTS_DIR)

        if not filepath:
            await ctx.send(f"No record found for {target.display_name} in this server.")
            return

        url = f"{public_url}/report/{guild_id}/{user_id}"
        await ctx.send(f"[{target.display_name}'s HR History]({url})")
