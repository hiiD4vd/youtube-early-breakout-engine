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
    def analyze(self, frame_path: str | None, transcript: str | None) -> GeminiFacts:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        mode = "image and transcript" if frame_path else "transcript only"
        prompt = f"""Analyze this YouTube Short using only the supplied {mode}.
Return one JSON object with exactly: niche (short neutral category), visual_facts (0-20 observable facts), transcript_summary, confidence (0..1).
Visual facts must be literally visible or directly stated: people, objects, colors, on-screen text, actions, and setting. Do not infer motives, performance strategy, virality, demographics, or advice. If evidence is unavailable, say so briefly rather than inventing it.
When no image is supplied, visual_facts must be an empty list.\nTranscript follows:\n""" + (transcript or "[No transcript available]")
        parts: list[dict] = [{"text": prompt}]
        if frame_path:
            image = base64.b64encode(Path(frame_path).read_bytes()).decode()
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image}})
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent", params={"key": settings.gemini_api_key}, json={"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}, timeout=45)
        response.raise_for_status()
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            facts = GeminiFacts.model_validate(json.loads(text))
            if not frame_path:
                facts.visual_facts = []
            return facts
        except Exception as exc:
            raise RuntimeError("Gemini returned invalid factual JSON") from exc
