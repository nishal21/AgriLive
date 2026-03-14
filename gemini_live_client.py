"""
gemini_live_client.py — Wrapper around the Google Gen AI SDK for Gemini Live API sessions.

Manages the stateful bidirectional audio/video streaming connection
with Google Search Grounding enabled.
"""

import asyncio
import contextlib
import logging
import os
from typing import AsyncGenerator, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("agrilive.gemini")

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
MODEL_ID = "gemini-live-2.5-flash-native-audio"

SYSTEM_INSTRUCTION = """You are **AgriBot**, a calm, empathetic, and knowledgeable agricultural extension officer from Kerala, India.

### Personality & Tone
- Speak in a warm, reassuring voice — like a trusted neighbour who also happens to be an expert.
- When a farmer sounds worried or distressed, mirror their concern first ("I understand this is worrying…") before providing advice.  Use the affective-dialog tone-matching capability to match the emotional register of the user.
- Keep every response SHORT — ideally 2-3 sentences. Farmers are busy; respect their time.
- You may greet in Malayalam ("Namaskaram!") but default to English unless the farmer switches language.

### Expertise
- Crop diseases, pest identification, soil health, organic remedies, irrigation advice — all calibrated for Kerala's tropical climate and common crops (rice, coconut, rubber, banana, pepper, cardamom, tea).
- When shown an image of a crop, describe what you see, identify possible diseases or pests, and suggest immediate next steps.
- If you are unsure of a diagnosis, say so honestly and recommend contacting the local Krishi Bhavan.

### Search Grounding — MANDATORY
- When a farmer asks about pests, weather, or crop diseases in their specific district (like Malappuram, Wayanad, Idukki, Thrissur, Palakkad, etc.), you MUST autonomously use the Google Search tool to find the most recent agricultural news, outbreak alerts, and Krishi Vigyan Kendra (KVK) advisories for that exact location before you synthesize your response.
- Always cite or mention the source of real-time information you find.

### Safety Guardrails — STRICTLY FOLLOW
1. **NEVER** recommend pesticides or chemicals that are banned by the Kerala State Government or the Central Insecticides Board (e.g., Endosulfan, Monocrotophos).  Always suggest safer, approved alternatives or organic methods.
2. If asked about chemicals you are unsure about, err on the side of caution and recommend the farmer consult their local agricultural officer.
3. **NEVER** provide medical advice for humans or animals beyond "please consult a doctor / veterinarian."
4. **NEVER** engage with requests related to self-harm, violence, or illegal activities. Respond with compassion and redirect the conversation.
5. Do not provide financial or legal advice. For subsidies or schemes, point to official government portals.
"""

# ---------------------------------------------------------------------------
# Tools — Google Search Grounding only (no custom functions)
# ---------------------------------------------------------------------------
LIVE_TOOLS = [
    types.Tool(google_search=types.GoogleSearch()),
]


# ---------------------------------------------------------------------------
# GeminiLiveClient
# ---------------------------------------------------------------------------
class GeminiLiveClient:
    """Manages a single Gemini Live API session."""

    def __init__(self):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options={'api_version': 'v1beta1'}
        )
        self._session = None
        self._closed = False

    @contextlib.asynccontextmanager
    async def connect(self):
        """Establish the Live API session and yield when ready."""
        
        # Use the formal types.LiveConnectConfig to avoid validation errors
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            # This must be the specific AudioTranscriptionConfig object
            output_audio_transcription=types.AudioTranscriptionConfig(
                model="latest" # Explicitly setting the model helps stability
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=SYSTEM_INSTRUCTION)]
            ),
            tools=LIVE_TOOLS,
        )

        try:
            async with self._client.aio.live.connect(model=MODEL_ID, config=config) as session:
                self._session = session
                logger.info("Gemini Live session connected.")
                yield
        except Exception as e:
            logger.error(f"Failed to establish Gemini Live connection: {e}")
            raise

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send a chunk of raw 16-bit PCM 16 kHz audio to the live session."""
        if self._session and not self._closed:
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=pcm_data,
                        )
                    ]
                )
            )

    async def send_video(self, jpeg_data: bytes) -> None:
        """Send a JPEG video frame to the live session."""
        if self._session and not self._closed:
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            mime_type="image/jpeg",
                            data=jpeg_data,
                        )
                    ]
                )
            )

    async def send_text(self, text: str) -> None:
        """Send a text message to the live session."""
        if self._session and not self._closed:
            await self._session.send(
                input=types.LiveClientContent(
                    turns=[
                        types.Content(
                            role="user", parts=[types.Part(text=text)]
                        )
                    ],
                    turn_complete=True,
                )
            )

    async def receive_responses(self) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields response messages from the Live session.
        Google Search is executed autonomously by the API — no local routing needed.
        """
        if not self._session:
            logger.warning("receive_responses called but self._session is None")
            return

        logger.info("Starting receive loop...")

        # FIX 3: Accumulate text chunks so we don't spam the UI with empty bubbles
        current_transcript = ""

        try:
            while True:
                try:
                    async for message in self._session.receive():
                        # --- Server content (audio / text) ---
                        if message.server_content:
                            sc = message.server_content

                            if sc.interrupted:
                                yield {"type": "interrupted"}
                                # Optional UI polish: show interrupted text with an ellipsis
                                if current_transcript.strip():
                                    yield {"type": "text", "data": current_transcript.strip() + "..."}
                                current_transcript = ""

                            # Handle incoming audio
                            if sc.model_turn and sc.model_turn.parts:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        yield {
                                            "type": "audio",
                                            "data": part.inline_data.data,
                                        }
                                    if hasattr(part, "text") and part.text:
                                        current_transcript += part.text

                            # Catch the built-in transcription chunks
                            if hasattr(sc, "output_transcription") and sc.output_transcription:
                                if hasattr(sc.output_transcription, "text") and sc.output_transcription.text:
                                    current_transcript += sc.output_transcription.text

                            if sc.turn_complete:
                                # Send the full sentence to the frontend as one neat bubble!
                                if current_transcript.strip():
                                    yield {"type": "text", "data": current_transcript.strip()}
                                    current_transcript = ""
                                yield {"type": "turn_complete"}
                except Exception as exc:
                    logger.warning("Session receive ended or connection dropped: %s", exc)
                    break

            logger.info("receive() loop ended naturally (StopAsyncIteration or disconnected).")
        except asyncio.CancelledError:
            logger.info("Receive loop cancelled.")
            raise
        except Exception as exc:
            logger.exception("Error in receive loop: %s", exc)
            raise
        finally:
            logger.info("Exiting receive_responses generator.")



