"""
main.py — FastAPI server for AgriLive: Multimodal Farm Assistant.

Serves the static frontend and exposes a WebSocket endpoint that bridges
the browser's audio/video streams to a Gemini Live API session.
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gemini_live_client import GeminiLiveClient
from crop_analyzer import analyze_crop_image


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("agrilive.server")

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
if not GOOGLE_CLOUD_PROJECT:
    logger.warning(
        "GOOGLE_CLOUD_PROJECT is not set. Live sessions will fail — "
        "set it via environment variable or .env file."
    )

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AgriLive — Multimodal Farm Assistant",
    version="0.1.0",
)

# Serve the static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """Serve the main page."""
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Crop Analysis endpoint (Multi-Agent: Vision Agent)
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    image: str  # base64-encoded JPEG


@app.post("/api/analyze")
async def analyze_crop(req: AnalyzeRequest):
    """Accept a base64 JPEG and return structured crop diagnosis."""
    try:
        logger.info("Received crop analysis request. Image size: %d bytes", len(req.image))
        result = await analyze_crop_image(req.image)
        logger.info("Analysis COMPLETE. Result: %s", str(result)[:300])
        return result
    except Exception as exc:
        logger.exception("Crop analysis failed: %s", exc)
        return {
            "species": "Unknown",
            "disease": None,
            "confidence_score": 0,
            "organic_remedies": [],
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Bidirectional WebSocket bridge between the browser and Gemini Live API.

    Client → Server message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM>"}
        {"type": "video", "data": "<base64-encoded JPEG>"}
        {"type": "text",  "data": "Hello"}

    Server → Client message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM>"}
        {"type": "text",  "data": "Response text"}
        {"type": "status","data": "connected | error | ..."}
    """
    await ws.accept()
    logger.info("WebSocket client connected.")

    if not GOOGLE_CLOUD_PROJECT:
        await ws.send_json(
            {"type": "status", "data": "error: GOOGLE_CLOUD_PROJECT not configured on the server."}
        )
        await ws.close(code=1008)
        return

    client = GeminiLiveClient()

    try:
        async with client.connect():
            await ws.send_json({"type": "status", "data": "connected"})

            # ---- Concurrent tasks: receive from Gemini, receive from browser ----

            async def _gemini_to_browser():
                """Forward responses from Gemini Live to the browser WebSocket."""
                try:
                    async for msg in client.receive_responses():
                        if msg["type"] == "audio":
                            audio_b64 = base64.b64encode(msg["data"]).decode("ascii")
                            await ws.send_json({"type": "audio", "data": audio_b64})
                        elif msg["type"] == "text":
                            await ws.send_json({"type": "text", "data": msg["data"]})
                        elif msg["type"] == "interrupted":
                            await ws.send_json({"type": "interrupted"})
                        elif msg["type"] == "turn_complete":
                            await ws.send_json({"type": "turn_complete"})
                except WebSocketDisconnect:
                    pass
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.exception("gemini→browser error: %s", exc)
                finally:
                    logger.info("_gemini_to_browser task finished.")

            async def _browser_to_gemini():
                """Forward audio/video/text from the browser WebSocket to Gemini Live."""
                try:
                    while True:
                        raw = await ws.receive_text()
                        message = json.loads(raw)
                        msg_type = message.get("type", "")
                        data = message.get("data", "")

                        if msg_type == "audio":
                            pcm_bytes = base64.b64decode(data)
                            await client.send_audio(pcm_bytes)

                        elif msg_type == "video":
                            jpeg_bytes = base64.b64decode(data)
                            await client.send_video(jpeg_bytes)

                        elif msg_type == "text":
                            await client.send_text(data)

                        elif msg_type == "ping":
                            # Client-to-server heartbeat
                            pass

                        else:
                            logger.warning("Unknown message type from client: %s", msg_type)

                except WebSocketDisconnect:
                    logger.info("WebSocket client disconnected.")
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.exception("browser→gemini error: %s", exc)
                finally:
                    logger.info("_browser_to_gemini task finished.")

            async def _heartbeat():
                """Send a keepalive ping every 30 seconds to prevent load balancer timeouts."""
                try:
                    while True:
                        await asyncio.sleep(10)
                        await ws.send_json({"type": "ping"})
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.exception("heartbeat error: %s", exc)
                finally:
                    logger.info("_heartbeat task finished.")

            # Run all directions concurrently
            gemini_task = asyncio.create_task(_gemini_to_browser())
            browser_task = asyncio.create_task(_browser_to_gemini())
            heartbeat_task = asyncio.create_task(_heartbeat())

            # Wait until any side finishes (e.g., browser disconnects)
            done, pending = await asyncio.wait(
                [gemini_task, browser_task, heartbeat_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for d in done:
                logger.info("Task completed: %s", d.get_name())
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.exception("Failed to connect to Gemini Live: %s", exc)
        try:
            await ws.send_json(
                {"type": "status", "data": f"error: Failed to connect — {exc}"}
            )
            await ws.close(code=1011)
        except Exception:
            pass
        return

    logger.info("Session cleaned up.")

