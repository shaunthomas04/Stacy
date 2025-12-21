import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
from rag_functions import ask_stacy_llm
import logging
import asyncio

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

# command that gives user input to llm for output
@bot.command(name="AskStacy")
async def ask_stacy_command(ctx, *, question: str):
    """
    Discord command that sends the user's question to the LLM and replies with the answer.
    """
    try:
        # Run the synchronous LLM function in a thread to avoid blocking the bot
        stacy_response = await asyncio.to_thread(ask_stacy_llm, question)

        if not stacy_response or not stacy_response.strip():
            stacy_response = (
                "⚠️ Stacy is temporarily unavailable, but your message was received."
            )

        await ctx.send(stacy_response[:1900])
        
    except Exception as e:
        logging.error(f"Error in ask_stacy_command: {e}")
        await ctx.send("⚠️ Sorry, Stacy did not receive your message properly. Please try again." + e)

# Run the bot
bot.run(TOKEN)
