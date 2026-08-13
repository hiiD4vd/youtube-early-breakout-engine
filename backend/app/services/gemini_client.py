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


class TopicClusterFacts(BaseModel):
    topic_title: str = Field(min_length=8, max_length=160)
    topic_type: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=8, max_length=400)
    entities: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)
    followable: bool


class MarketSemanticFacts(BaseModel):
    """Compact, auditable semantic fingerprint for one market Short."""
    topic_label: str = Field(min_length=2, max_length=160)
    topic_type: str = Field(min_length=2, max_length=64)
    entities: list[str] = Field(default_factory=list, max_length=8)
    event_context: str = Field(default="", max_length=160)
    content_format: str = Field(default="other", max_length=64)
    topic_theme: str = Field(default="", max_length=160)
    theme_confidence: float = Field(default=0, ge=0, le=1)
    summary: str = Field(default="", max_length=360)
    confidence: float = Field(ge=0, le=1)


class GeminiClient:
    def analyze_market_semantics(self, title: str, description: str | None) -> MarketSemanticFacts:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        prompt = f"""Extract an auditable semantic fingerprint for one YouTube Short.
Use only the supplied title and description. Do not infer a film, event, news story, or relationship merely from celebrity names. Ignore copyright text, URLs, generic hashtags, and channel branding.
Return exactly one JSON object: topic_label, topic_type, entities, event_context, content_format, topic_theme, theme_confidence, summary, confidence.
topic_label must be concise when evidence supports it; otherwise use a neutral label such as 'Unclear entertainment clip'.
event_context must be empty unless explicit in the text. entities are named people, teams, films, products, places, or events explicitly mentioned.
topic_theme is a broader but still followable group; never use a bare generic category such as Sports, Funny, Viral, or Music. Leave it empty if unsupported.
Title: {title}\nDescription: {description or ''}"""
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent",
            params={"key": settings.gemini_api_key},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
            timeout=45,
        )
        response.raise_for_status()
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            payload = json.loads(text)
            if isinstance(payload, dict):
                entities = payload.get("entities")
                if isinstance(entities, list):
                    payload["entities"] = [item for item in entities if isinstance(item, str) and item.strip()][:8]
                for field, limit in (("topic_label", 160), ("topic_type", 64), ("event_context", 160), ("content_format", 64), ("topic_theme", 160), ("summary", 360)):
                    if isinstance(payload.get(field), str):
                        payload[field] = payload[field][:limit]
                try:
                    payload["theme_confidence"] = max(0.0, min(1.0, float(payload.get("theme_confidence") or 0)))
                except (TypeError, ValueError):
                    payload["theme_confidence"] = 0.0
            return MarketSemanticFacts.model_validate(payload)
        except Exception as exc:
            raise RuntimeError("Gemini returned invalid market semantic JSON") from exc

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

    def analyze_topic_cluster(self, evidence: str) -> TopicClusterFacts:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        prompt = """You label a cluster of independently collected YouTube Shorts.
Return exactly one JSON object with: topic_title, topic_type, summary, entities, confidence, followable.
First resolve repeated named entities across titles and captions. If a shared person or pair is clear, make the title actionable and contextual, for example 'Zendaya & Tom Holland — celebrity discussion clips', rather than a lone name or a raw word. You may use stable public knowledge only to disambiguate an entity, never to invent the current reason it is trending. Do NOT label a cluster as a specific film, show, event, relationship, or news story unless that exact context is explicitly present in at least one supplied title or caption. Name a concrete event, person-plus-context, meme, challenge, or story only when the supplied evidence genuinely supports one shared topic. Never use generic words such as viral, funny, kick, goal, shorts, edit, music, sport, or a lone person name as the title. If evidence is broad, unrelated, or insufficient, set followable false and use title 'Insufficient shared topic'. Do not claim global popularity.
Cluster evidence follows:\n""" + evidence
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent", params={"key": settings.gemini_api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}, timeout=45)
        response.raise_for_status()
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return TopicClusterFacts.model_validate(json.loads(text))
        except Exception as exc:
            raise RuntimeError("Gemini returned invalid topic JSON") from exc

    def same_topic(self, early_label: str, market_label: str) -> tuple[bool, float]:
        """Bounded semantic comparison used only for delayed evaluation."""
        if not settings.gemini_api_key:
            return False, 0.0
        prompt = f'''Compare two observed YouTube topic labels. Are they the same underlying conversation or event? Do not treat a shared broad category (for example football) as a match. Return JSON exactly: {{"same_topic": boolean, "confidence": number}}.
Early topic: {early_label}
Later market topic: {market_label}'''
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent", params={"key": settings.gemini_api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}, timeout=30)
        response.raise_for_status()
        try:
            payload = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
            confidence = float(payload.get("confidence", 0))
            return bool(payload.get("same_topic")) and confidence >= .80, confidence
        except Exception as exc:
            raise RuntimeError("Gemini returned invalid outcome JSON") from exc
