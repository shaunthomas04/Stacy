"""
Serves generated HR reports over HTTP and tunnels them to the internet
via ngrok, so !history links work outside the local network.
"""
import logging
import os
import threading

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pyngrok import ngrok
import uvicorn

import config

logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "temp")
os.makedirs(REPORTS_DIR, exist_ok=True)

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


def _start_api():
    uvicorn.run(api, host="0.0.0.0", port=config.REPORT_PORT, log_level="warning")


def start_report_server() -> str:
    """
    Starts the FastAPI report server in a background thread and opens
    an ngrok tunnel to it. Returns the public URL reports are served from.
    """
    ngrok.set_auth_token(config.NGROK_AUTHTOKEN)
    threading.Thread(target=_start_api, daemon=True).start()
    public_url = ngrok.connect(config.REPORT_PORT).public_url
    logger.info(f"Report server live at: {public_url}")
    return public_url
