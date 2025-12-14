#!/bin/bash

# Name of the model
MODEL="qwen3:0.6b"

# Check if Ollama model is running
if pgrep -f "ollama run $MODEL" > /dev/null
then
    echo "✅ Ollama model $MODEL is already running."
else
    echo "⚡ Starting Ollama model $MODEL..."
    # Start the Ollama model in the background
    nohup ollama run $MODEL > ollama.log 2>&1 &
    sleep 5  # wait a few seconds for the model to start
fi

# Activate virtual environment
source .venv/bin/activate

# Run the Discord bot
echo "🚀 Starting Stacy bot..."
python src/bot.py
