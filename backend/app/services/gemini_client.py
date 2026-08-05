import base64
import json
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from app.config import settings


class GeminiFacts(BaseModel):
    niche: str = Field(min_length=1, max_length=128)
    visual_facts: list[str] = Field(default_factory=list, max_length=20)
    transcript_summary: str = ""
    confidence: float = Field(ge=0, le=1)


class GeminiClient:
    def analyze(self, frame_path: str, transcript: str | None) -> GeminiFacts:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        prompt = "Return JSON only: niche, visual_facts (observable facts only), transcript_summary, confidence 0..1. No advice, strategy, or invented facts. Transcript: " + (transcript or "[unavailable]")
        image = base64.b64encode(Path(frame_path).read_bytes()).decode()
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent", params={"key": settings.gemini_api_key}, json={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": image}}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}, timeout=45)
        response.raise_for_status()
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return GeminiFacts.model_validate(json.loads(text))
        except Exception as exc:
            raise RuntimeError("Gemini returned invalid factual JSON") from exc
