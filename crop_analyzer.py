"""
crop_analyzer.py — Async Crop Analysis Agent

Uses Gemini 2.5 Flash via Vertex AI to analyze a crop image and return
structured diagnosis data (species, disease, confidence, organic remedies).
"""
import base64
import logging
import os
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

logger = logging.getLogger("agrilive.analyzer")

ANALYSIS_MODEL = "gemini-1.5-pro-002"

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

    Returns a dict with: species, disease, confidence_score, organic_remedies.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")

    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options={'api_version': 'v1beta1'}
    )

    import base64
    
    # Strip the base64 prefix if present (e.g., "data:image/jpeg;base64,")
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
        
    image_bytes = base64.b64decode(image_b64)

    try:
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
                response_mime_type="application/json",
                response_schema=CropDiagnosis,
            ),
        )

        # Use response.parsed
        result = response.parsed
        raw_text = response.text if hasattr(response, 'text') else ""
        logger.info("[CropAnalyzer] Raw response text: %s", raw_text)
        
        if result is None:
            logger.warning("[CropAnalyzer] response.parsed is None, attempting manual JSON parse...")
            import json
            import re
            # Extract JSON if wrapped in markdown blocks
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                try:
                    result_dict = json.loads(json_match.group())
                    # Validate against model_dump like structure
                    return {
                        "species": result_dict.get("species", "Unknown"),
                        "disease": result_dict.get("disease", "None"),
                        "confidence_score": result_dict.get("confidence_score", 0),
                        "organic_remedies": result_dict.get("organic_remedies", [])
                    }
                except Exception as e:
                    logger.error("[CropAnalyzer] Manual parse failed: %s", e)

        if result is None:
            raise ValueError("Gemini returned an empty parsed response.")
            
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result

    except Exception as exc:
        logger.error("Crop analysis or parsing failed: %s", exc)
        return {
            "species": "Unknown",
            "disease": "Could not analyze image",
            "confidence_score": 0,
            "organic_remedies": [],
        }
