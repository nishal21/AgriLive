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
MODELS_TO_TRY = ["gemini-1.5-pro-002", "gemini-1.5-pro", "gemini-1.5-flash"]

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
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")

    client = genai.Client(
        vertexai=True,
        project=project,
        location=location
    )

    # Strip the base64 prefix if present
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
    image_bytes = base64.b64decode(image_b64)

    last_error = None
    for model_id in MODELS_TO_TRY:
        try:
            logger.info("[CropAnalyzer] Attempting diagnosis with: %s", model_id)
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

            # 1. Try standard parsing
            result = response.parsed
            raw_text = response.text if hasattr(response, 'text') else ""
            
            # 2. Manual fallback if SDK parsing fails
            if result is None:
                logger.warning("[CropAnalyzer] SDK parsing failed for %s, trying manual JSON extraction", model_id)
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
                    except Exception as e:
                        logger.error("[CropAnalyzer] Manual JSON parse failed: %s", e)
                
                raise ValueError(f"Model {model_id} returned empty or unparseable result.")

            if hasattr(result, "model_dump"):
                return result.model_dump()
            return result

        except Exception as exc:
            logger.warning("[CropAnalyzer] Model %s failed: %s", model_id, exc)
            last_error = exc
            # If it's a 404 or permission error, proceed to next model
            continue

    # Fallback if both Pro and Flash fail completely
    logger.error("[CropAnalyzer] All analysis tiers failed. Last error: %s", last_error)
    return {
        "species": "Unknown",
        "disease": "Diagnosis currently unavailable",
        "confidence_score": 0,
        "organic_remedies": [],
        "debug_error": str(last_error)[:100]
    }
