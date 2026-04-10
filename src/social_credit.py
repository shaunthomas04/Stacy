import discord
import os
from dotenv import load_dotenv
from database_functions import get_user_score, upsert_user

# ---------------------------
# CONFIG
# ---------------------------

ROLE_THRESHOLDS = [
    (0,  "HR Approved"),
    (1,  "Under Review"),
    (6,  "Suspended Pay"),
    (16, "Blacklisted"),
]

ROLE_COLORS = {
    "HR Approved":  discord.Color.green(),
    "Under Review": discord.Color.yellow(),
    "Suspended Pay": discord.Color.orange(),
    "Blacklisted":  discord.Color.red(),
}

# ---------------------------
# CORE LOGIC
# ---------------------------

def get_role_for_score(score: int) -> str:
    role_name = ROLE_THRESHOLDS[0][1]
    for threshold, name in ROLE_THRESHOLDS:
        if score >= threshold:
            role_name = name
    return role_name


async def get_or_create_role(
    guild: discord.Guild,
    role_name: str,
    fetched_roles: list
) -> discord.Role:
    existing = discord.utils.get(fetched_roles, name=role_name)
    if existing:
        return existing

    new_role = await guild.create_role(
        name=role_name,
        color=ROLE_COLORS.get(role_name, discord.Color.default()),
        reason="Stacy HR role auto-created"
    )
    print(f"  Created new role: {role_name} in {guild.name}")
    return new_role


async def assign_hr_role(
    guild: discord.Guild,
    member: discord.Member,
    score: int,
    fetched_roles: list
):
    correct_role_name = get_role_for_score(score)
    hr_role_names = {name for _, name in ROLE_THRESHOLDS}

    roles_to_remove = [r for r in member.roles if r.name in hr_role_names]
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Stacy HR role update")

    role = await get_or_create_role(guild, correct_role_name, fetched_roles)
    await member.add_roles(role, reason=f"Stacy HR: score={score}")
    print(f"  ✅ {member.display_name} → '{correct_role_name}' (score: {score})")


async def sync_all_roles(guild: discord.Guild):
    """
    Goes through every member in the guild, pulls their score from the DB,
    and assigns the correct HR role. Safe to call from a cron job.
    """
    print(f"\n🔄 Starting HR role sync for {guild.name}...")

    # Ensure all HR roles exist up front
    fetched_roles = await guild.fetch_roles()
    for _, role_name in ROLE_THRESHOLDS:
        await get_or_create_role(guild, role_name, fetched_roles)
    fetched_roles = await guild.fetch_roles()  # refresh after any creations

    # Iterate every non-bot member
    synced = 0
    skipped = 0
    async for member in guild.fetch_members(limit=None):
        if member.bot:
            skipped += 1
            continue

        # Make sure they exist in DB (safe no-op if already there)
        upsert_user(str(member.id), str(guild.id), member.display_name)

        score = get_user_score(str(member.id), str(guild.id))
        await assign_hr_role(guild, member, score, fetched_roles)
        synced += 1

    print(f"✅ Sync complete — {synced} members updated, {skipped} bots skipped\n")


async def setup_and_assign_hr_role(
    guild: discord.Guild,
    member: discord.Member,
    score: int
):
    """Single-member assignment. Used by the bot after an infraction."""
    fetched_roles = await guild.fetch_roles()
    for _, role_name in ROLE_THRESHOLDS:
        await get_or_create_role(guild, role_name, fetched_roles)
    fetched_roles = await guild.fetch_roles()
    await assign_hr_role(guild, member, score, fetched_roles)


# ---------------------------
# MAIN — runs the full guild sync
# ---------------------------

load_dotenv()
TOKEN    = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
USER_ID  = int(os.getenv("USER_ID"))

if __name__ == "__main__":
    import asyncio

    async def run_sync():
        intents = discord.Intents.default()
        intents.members = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            print(f"Logged in as {client.user}")

            guild = await client.fetch_guild(GUILD_ID)
            await sync_all_roles(guild)

            await client.close()

        await client.start(TOKEN)

    asyncio.run(run_sync())