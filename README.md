# Stacy HR Bot

A Discord bot powered by Ollama and Qwen 3.

## Quickstart

### Prerequisites

- Python 3.8 or higher
- Ollama

### Setup Instructions

1. **Create a Python virtual environment**
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment**
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   
   Create a `.env` file in the project root and add your Discord bot token:
   ```
   DISCORD_TOKEN=YOUR_TOKEN_HERE
   ```
   Replace `YOUR_TOKEN_HERE` with the token Shaun provides you.

5. **Install Ollama**
   
   Download and install Ollama from: https://ollama.com/download

6. **Pull the Qwen 3 model**
   ```bash
   ollama pull qwen3:0.6b
   ```
   
   **Note:** You can use any Ollama model that supports tool calling. If you choose a different model:
   - Pull your preferred model: `ollama pull model-name`
   - Update the model name in the bot functions/code to match your chosen model
   - Ensure the model supports tools/function calling (check the model documentation)

7. **Run the Ollama model**
   ```bash
   ollama run qwen3:0.6b
   ```
   Keep this terminal window open while running the bot.

8. **Run the bot**
   
   In a new terminal window (with your virtual environment activated):
   ```bash
   python bot.py
   ```

### Inviting the Bot to Your Server

Use this link to invite the bot to your Discord server:

https://discord.com/oauth2/authorize?client_id=1449588724265521154&scope=bot&permissions=586514329238594

---

**Note:** Make sure Ollama is running before starting the bot.
