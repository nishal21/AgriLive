"""
crop_analyzer.py — Async Crop Analysis Agent

Uses Gemini 2.5 Flash via Vertex AI to analyze a crop image and return
structured diagnosis data (species, disease, confidence, organic remedies).
"""

import json
import logging
import os
import re

from google import genai
from google.genai import types

logger = logging.getLogger("agrilive.analyzer")

ANALYSIS_MODEL = "gemini-2.5-flash"

ANALYSIS_PROMPT = """You are an expert agronomist specializing in tropical crops from Kerala, India.

Analyze this crop image carefully. Return ONLY a valid JSON object with these exact fields:

{
    "species": "Name of the plant/crop species",
    "disease": "Name of the disease or issue detected, or null if the plant looks healthy",
    "confidence_score": 85,
    "organic_remedies": ["remedy 1", "remedy 2"]
}

Rules:
- confidence_score must be an integer between 0 and 100
- If no disease is found, set disease to null and organic_remedies to an empty list
- Focus on crops common in Kerala: rice, coconut, rubber, banana, pepper, cardamom, tea, arecanut
- NEVER recommend banned chemicals. Only suggest organic or approved remedies.
- If you cannot identify the crop or the image is unclear, set species to "Unknown" and confidence_score to a low value.
- Return ONLY the JSON object, no markdown, no commentary.
"""


async def analyze_crop_image(image_b64: str) -> dict:
    """
    Analyze a base64-encoded JPEG image of a crop.

    Returns a dict with: species, disease, confidence_score, organic_remedies.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")

    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
    )

    import base64
    
    # Strip the base64 prefix if present (e.g., "data:image/jpeg;base64,")
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
        
    image_bytes = base64.b64decode(image_b64)

    response = await client.aio.models.generate_content(
        model=ANALYSIS_MODEL,
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
            max_output_tokens=512,
        ),
    )

    # Extract text from response
    text = response.text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse Gemini response as JSON: %s", text)
        result = {
            "species": "Unknown",
            "disease": None,
            "confidence_score": 0,
            "organic_remedies": [],
        }

    # Ensure all required fields exist
    result.setdefault("species", "Unknown")
    result.setdefault("disease", None)
    result.setdefault("confidence_score", 0)
    result.setdefault("organic_remedies", [])

    return result
