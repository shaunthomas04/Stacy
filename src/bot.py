import os
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Load token from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Intents (required to read messages)
intents = discord.Intents.default()
intents.message_content = True

# Bot setup
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------
# Events
# ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

# ------------------
# Commands
# ------------------
@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def hello(ctx):
    await ctx.send(f"Hello {ctx.author.mention}, HR acknowledges your presence.")

@bot.command()
async def rules(ctx):
    await ctx.send(
        "**HR Guidelines:**\n"
        "1. Be respectful\n"
        "2. No harassment\n"
        "3. All memes must be HR-approved"
    )

@bot.command()
async def helpme(ctx):
    await ctx.send(
        "**Available Commands:**\n"
        "`!ping` – Test the bot\n"
        "`!hello` – Greet HR\n"
        "`!rules` – Read company rules\n"
        "`!helpme` – List commands"
    )

# Run the bot
bot.run(TOKEN)
