#!/bin/bash

MODEL="qwen2.5:7b"
OLLAMA_URL="http://localhost:11434"

echo "🔍 Checking Ollama server..."

until curl -s "$OLLAMA_URL/api/tags" > /dev/null; do
    echo "⏳ Waiting for Ollama server..."
    sleep 2
done

echo "✅ Ollama server is up."

echo "🔍 Checking model availability..."

until curl -s -X POST "$OLLAMA_URL/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"ping\",\"stream\":false}" \
    > /dev/null; do
    echo "⏳ Waiting for model $MODEL to load..."
    sleep 3
done

echo "✅ Model $MODEL is ready."

# Activate virtual environment
source .venv/bin/activate

echo "🚀 Starting Stacy bot..."
python src/bot.py
