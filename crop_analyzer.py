"""
crop_analyzer.py — Async Crop Analysis Agent

Uses Gemini 1.5 Pro (with Flash fallback) via Vertex AI to analyze a crop image 
and return structured diagnosis data (species, disease, confidence, organic remedies).
"""
import base64
import logging
import os
import json
import re
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

logger = logging.getLogger("agrilive.analyzer")

# Models to try in order of "intelligence"
MODELS_TO_TRY = ["gemini-1.5-pro-002", "gemini-1.5-pro", "gemini-1.5-flash-002", "gemini-1.5-flash"]

class CropDiagnosis(BaseModel):
    species: str = Field(description="Common name of the plant/crop")
    disease: str = Field(description="Specific disease/pest name, or 'None' if healthy") 
    confidence_score: int = Field(description="Confidence score between 0 and 100")
    organic_remedies: list[str] = Field(description="List of practical organic remedies")

ANALYSIS_PROMPT = """You are an expert agronomist specialized in tropical crops, with deep knowledge of agriculture in Kerala, India.

Analyze this crop image carefully and provide a diagnosis.
Rules:
- confidence_score must be an integer between 0 and 100
- If no disease is found, set disease to 'None' and organic_remedies to an empty list
- Focus on crops common in Kerala: rice, coconut, rubber, banana, pepper, cardamom, tea, arecanut
- NEVER recommend banned chemicals. Only suggest organic or approved remedies.
- If you cannot identify the crop or the image is unclear, set species to "Unknown" and confidence_score to a low value.
"""


async def analyze_crop_image(image_b64: str) -> dict:
    """
    Analyze a base64-encoded JPEG image of a crop.
    Attempts Pro first, then falls back to Flash if restricted or unavailable.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"

    logger.info("[Analyzer] System Check: Project=%s, Location=%s", project, location)

    # Use v1 stable instead of v1beta1 to avoid model mismatch
    try:
        client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options={'api_version': 'v1'}
        )
        logger.info("[Analyzer] Vertex AI Client (v1) initialized.")
    except Exception as e:
        logger.error("[Analyzer] Client init failed: %s", e)
        raise

    # Strip the base64 prefix if present
    try:
        if "," in image_b64:
            image_b64 = image_b64.split(",")[1]
        image_b64 = "".join(image_b64.split())
        image_bytes = base64.b64decode(image_b64)
        logger.info("[Analyzer] Image decoded: %d bytes", len(image_bytes))
    except Exception as e:
        logger.error("[Analyzer] Decode failed: %s", e)
        raise

    last_error = None
    for model_id in MODELS_TO_TRY:
        try:
            logger.info("[CropAnalyzer] Trying model: %s", model_id)
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(text=ANALYSIS_PROMPT),
                            types.Part(
                                inline_data=types.Blob(
                                    mime_type="image/jpeg",
                                    data=image_bytes,
                                )
                            ),
                        ]
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                    response_schema=CropDiagnosis,
                ),
            )

            result = response.parsed
            raw_text = response.text if hasattr(response, 'text') else ""
            
            if result is None:
                logger.warning("[CropAnalyzer] SDK parsing failed, trying manual JSON extraction")
                json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group())
                        return {
                            "species": data.get("species", "Unknown"),
                            "disease": data.get("disease", "None"),
                            "confidence_score": data.get("confidence_score", 0),
                            "organic_remedies": data.get("organic_remedies", [])
                        }
                    except Exception:
                        pass
                raise ValueError(f"Model {model_id} returned unparseable result.")

            return result.model_dump() if hasattr(result, "model_dump") else result

        except Exception as exc:
            logger.warning("[CropAnalyzer] Model %s failed: %s", model_id, exc)
            last_error = exc
            
            # If 404, quickly try the next model
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                continue
            
            # If other error, still try fallback
            continue

    # Final Fallback Attempt: List models to log what's actually available
    try:
        logger.info("[Analyzer] DISCOVERY: Listing available models in this project...")
        for m in client.models.list():
            logger.info("  Available: %s", m.name)
    except:
        pass

    logger.error("[CropAnalyzer] All analysis tiers failed. Last error: %s", last_error)
    return {
        "species": "Unknown",
        "disease": "Diagnosis currently unavailable",
        "confidence_score": 0,
        "organic_remedies": [],
        "debug_error": str(last_error)[:100]
    }
