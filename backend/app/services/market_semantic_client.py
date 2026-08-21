"""OpenAI-compatible client for Market Topic semantic fingerprints."""
from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.services.gemini_client import MarketSemanticFacts, TopicClusterFacts, TopicClusterGroupFacts


class ContentTruthFacts(BaseModel):
    """Strictly factual comparison of a Short's metadata and visible content."""

    title_claim: str = ""
    content_summary: str = ""
    visual_summary: str = ""
    content_entities: list[str] = Field(default_factory=list)
    content_topic_label: str = ""
    content_topic_type: str = "other"
    content_event_context: str = ""
    verdict: str = "INCONCLUSIVE"
    alignment_score: float = 0.0
    confidence: float = 0.0
    mismatch_reason: str = ""


class MarketSemanticClient:
    @staticmethod
    def _first_json(value: str) -> dict:
        """Read the first JSON object from gateways that append transport metadata."""
        decoder = json.JSONDecoder()
        start = value.find("{")
        if start < 0:
            raise ValueError("no JSON object found")
        parsed, _ = decoder.raw_decode(value[start:])
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed

    def _request(self, prompt: str, *, model: str | None = None, image_url: str | None = None) -> dict:
        endpoint = settings.market_semantic_base_url.rstrip("/") + "/chat/completions"
        content: str | list[dict] = prompt
        if image_url:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {settings.market_semantic_api_key}"},
            json={
                "model": model or settings.market_semantic_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        response.raise_for_status()
        envelope = self._first_json(response.text)
        message = envelope["choices"][0]["message"]
        content = message.get("content") or message.get("reasoning_content") or ""
        return self._first_json(content) if isinstance(content, str) else content

    @staticmethod
    def _normalize_confidence(payload: dict) -> None:
        confidence = payload.get("confidence")
        if isinstance(confidence, str):
            normalized = confidence.strip().lower()
            payload["confidence"] = {
                "very high": 0.95, "high": 0.85, "medium": 0.65,
                "low": 0.4, "very low": 0.2,
            }.get(normalized, 0.5)

    def analyze(self, title: str, description: str | None, transcript: str | None = None) -> MarketSemanticFacts:
        if not settings.market_semantic_api_key or not settings.market_semantic_base_url:
            raise RuntimeError("MARKET_SEMANTIC provider is not configured")
        transcript_block = (transcript or "").strip()
        transcript_hint = f"\nTranscript: {transcript_block[:4000]}" if transcript_block else "\nTranscript: [No transcript available]"
        prompt = f"""Extract an auditable semantic fingerprint for one YouTube Short.
Use the title, description, and transcript (when present). Treat the transcript as the ground truth for what the video actually shows; a clickbait title must never override it. Do not invent a film, event, or relationship from names alone. Ignore copyright text, URLs, generic hashtags, and channel branding.
Return exactly JSON with: topic_label, topic_type, entities, event_context, content_format, topic_theme, theme_confidence, summary, confidence.
event_context is empty unless explicitly supported. entities are explicit named people, teams, films, products, places, or events.
topic_theme is a broader human-followable conversation group supported by this Short, for example "football player highlights and fan clips", "celebrity interviews and trivia", or "satisfying craft videos". It must not be a bare generic word such as Sports, Music, Funny, or Viral. Leave it empty when no honest broad theme is possible. Reuse the exact same phrase whenever two Shorts belong to the same conversation group; never invent near-duplicate variants or append noise qualifiers like "and fan clips".
Title: {title}\nDescription: {description or ''}{transcript_hint}"""
        try:
            payload = self._request(prompt)
            self._normalize_confidence(payload)
            if isinstance(payload.get("entities"), list):
                payload["entities"] = [item for item in payload["entities"] if isinstance(item, str) and item.strip()][:8]
            for field, limit in (("topic_label", 160), ("topic_type", 64), ("event_context", 160), ("content_format", 64), ("topic_theme", 160), ("summary", 360)):
                if isinstance(payload.get(field), str):
                    payload[field] = payload[field][:limit]
            try:
                payload["theme_confidence"] = max(0.0, min(1.0, float(payload.get("theme_confidence") or 0)))
            except (TypeError, ValueError):
                payload["theme_confidence"] = 0.0
            return MarketSemanticFacts.model_validate(payload)
        except httpx.HTTPError:
            # Preserve transport/status failures so the queue can report a
            # genuine gateway outage rather than pretending the model emitted
            # malformed JSON.
            raise
        except Exception as exc:
            raise RuntimeError("Semantic gateway returned invalid JSON") from exc

    def review_topic_cluster(self, evidence: str) -> TopicClusterFacts:
        """Use the stronger review model only after cross-channel evidence exists."""
        prompt = """You are the final reviewer for a candidate YouTube Shorts trend.
Return exactly JSON: topic_title, topic_type, summary, entities, confidence, followable.
Use only the evidence supplied. A shared person alone is not enough to claim a current film, event, relationship, or news story. Reject mixed clips, generic categories, and keyword-only matches by setting followable false. When evidence is coherent, write one human-readable label that people could follow.
Never claim YouTube-wide popularity or invent missing context.
Candidate evidence follows:\n""" + evidence
        try:
            payload = self._request(prompt, model=settings.market_topic_review_model or settings.market_semantic_model)
            self._normalize_confidence(payload)
            # OpenAI-compatible gateways do not all honour JSON schema equally.
            # A rejected/mixed cluster commonly comes back with an empty
            # ``topic_type`` (and occasionally an empty label/summary).  That is
            # still a valid *negative* review and must not crash the whole batch.
            title = str(payload.get("topic_title") or "").strip()
            topic_type = str(payload.get("topic_type") or "").strip()
            summary = str(payload.get("summary") or "").strip()
            if len(title) < 8:
                title = "Unclear mixed topic"
            if len(topic_type) < 2:
                topic_type = "other"
            if len(summary) < 8:
                summary = "Evidence does not support one coherent followable topic."
            payload["topic_title"] = title
            payload["topic_type"] = topic_type
            payload["summary"] = summary
            try:
                payload["confidence"] = max(0.0, min(1.0, float(payload.get("confidence") or 0)))
            except (TypeError, ValueError):
                payload["confidence"] = 0.0
            followable = payload.get("followable", False)
            if isinstance(followable, str):
                followable = followable.strip().lower() in {"true", "yes", "1"}
            payload["followable"] = bool(followable)
            if isinstance(payload.get("entities"), list):
                payload["entities"] = [item for item in payload["entities"] if isinstance(item, str) and item.strip()][:12]
            else:
                payload["entities"] = []
            for field, limit in (("topic_title", 160), ("topic_type", 80), ("summary", 400)):
                if isinstance(payload.get(field), str):
                    payload[field] = payload[field][:limit]
            return TopicClusterFacts.model_validate(payload)
        except httpx.HTTPError:
            raise
        except Exception as exc:
            raise RuntimeError("Topic review gateway returned invalid JSON") from exc

    def group_topic_candidates(self, evidence: str) -> list[TopicClusterGroupFacts]:
        """Group Shorts by a shared subject, event, or creative format."""
        prompt = """You are grouping independently collected YouTube Shorts for a human-facing topic explorer.
Return exactly one JSON object with a `groups` array. Every group must contain:
topic_title, topic_type, summary, entities, confidence, followable, video_indices.

Rules:
- Every group needs at least 2 different video indices.
- A topic may be the same real-world subject/event OR the same recognisable content format.
- Prefer a truthful broad format when objects differ but the mechanic is the same.
  Different ranked subjects -> "Video ranking dan hitung mundur".
  Different experiments using "what happens if" -> "Eksperimen 'apa yang terjadi jika'".
- A title must be a complete Indonesian noun phrase a normal person understands.
- NEVER concatenate frequent title words. Invalid labels include
  "Apa · Jika · Saling", "Sea · Lion · Moments", and "Top · Best · Turtle".
- Hook words such as apa, jika, inilah, saling, her, who, best, and top are not subjects.
- Do not claim a specific event/person when videos only share a format.
- Do not merge unrelated formats merely because all are funny, viral, or Shorts.
- `topic_type` should identify the useful axis: format_ranking,
  format_what_if, animals, sports, gaming, food, diy, event, or other.
- Use only valid zero-based indices. Leave genuine singletons out.
- `followable` means coherent enough to explore, not already a confirmed trend.

Evidence follows:
""" + evidence
        try:
            # First-pass grouping runs over many batches. Use the configured
            # semantic model here; the stronger/slower review model remains
            # reserved for the final cross-channel quality gate.
            payload = self._request(prompt, model=settings.market_semantic_model)
            raw_groups = payload.get("groups")
            if not isinstance(raw_groups, list):
                raise ValueError("groups must be an array")
            groups: list[TopicClusterGroupFacts] = []
            for item in raw_groups:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("topic_title") or "").strip()
                topic_type = str(item.get("topic_type") or "other").strip() or "other"
                summary = str(item.get("summary") or "").strip()
                if len(title) < 4 or len(summary) < 8:
                    continue
                item["topic_title"] = title[:160]
                item["topic_type"] = topic_type[:80]
                item["summary"] = summary[:400]
                entities = item.get("entities")
                item["entities"] = [entry.strip() for entry in entities if isinstance(entry, str) and entry.strip()][:12] if isinstance(entities, list) else []
                indices = item.get("video_indices")
                item["video_indices"] = list(dict.fromkeys(int(idx) for idx in indices if isinstance(idx, int) and idx >= 0))[:50] if isinstance(indices, list) else []
                try:
                    item["confidence"] = max(0.0, min(1.0, float(item.get("confidence") or 0)))
                except (TypeError, ValueError):
                    item["confidence"] = 0.0
                followable = item.get("followable", False)
                if isinstance(followable, str):
                    followable = followable.strip().lower() in {"true", "yes", "1"}
                item["followable"] = bool(followable)
                if len(item["video_indices"]) >= 2:
                    groups.append(TopicClusterGroupFacts.model_validate(item))
            return groups
        except httpx.HTTPError:
            raise
        except Exception as exc:
            raise RuntimeError("Topic grouping gateway returned invalid JSON") from exc

    def same_topic(self, early_label: str, market_label: str) -> tuple[bool, float]:
        """Bounded GPT comparison for delayed early-signal evaluation."""
        prompt = f"""Compare two observed YouTube Shorts topic labels.
Return exactly JSON: same_topic, confidence. A shared broad category such as
football is not enough; they must be the same underlying conversation or event.
Early Topic: {early_label}
Market Topic: {market_label}"""
        try:
            payload = self._request(prompt, model=settings.market_topic_review_model or settings.market_semantic_model)
            self._normalize_confidence(payload)
            confidence = float(payload.get("confidence") or 0)
            return bool(payload.get("same_topic")) and confidence >= 0.80, confidence
        except httpx.HTTPError:
            # An unavailable provider is not evidence that two topics differ.
            # Let the background task retain the benchmark as PENDING.
            raise
        except Exception:
            return False, 0.0

    def analyze_content_truth(self, *, title: str, transcript: str | None, thumbnail_url: str | None) -> ContentTruthFacts:
        """Check whether the actual Short supports its title's central claim.

        This is not a virality or bot detector. It only classifies observable
        title/content agreement so SEO-hijacked metadata cannot create a false
        event trend.
        """
        if not settings.market_semantic_api_key or not settings.market_semantic_base_url:
            raise RuntimeError("MARKET_SEMANTIC provider is not configured")
        prompt = f"""You are verifying whether a YouTube Short's title matches its actual content.
Use only the transcript and, if visible, the supplied thumbnail. Never assume the title is true.
An event/person claim is ALIGNED only when the content substantively discusses or visibly depicts that same claim.
If the title says sports/news/celebrity but the content is an unrelated prank, recipe, animation, satisfying clip, meme, or generic footage, return MISMATCH.
If evidence is missing or ambiguous, return INCONCLUSIVE. Do not infer bot activity or fake views.
Also derive a neutral label for the actual content itself. Return exactly JSON with: title_claim, content_summary, visual_summary, content_entities, content_topic_label, content_topic_type, content_event_context, verdict, alignment_score, confidence, mismatch_reason.
verdict must be ALIGNED, MISMATCH, or INCONCLUSIVE. Scores are 0..1.
content_topic_label must describe only the actual transcript/visual evidence; it may be broad (for example "football highlights") when no specific event is proven. content_event_context is empty unless explicitly supported by content.
Title: {title}
Transcript: {(transcript or '[No transcript available]')[:6000]}"""
        try:
            payload = self._request(prompt, model=settings.market_topic_review_model or settings.market_semantic_model, image_url=thumbnail_url)
            self._normalize_confidence(payload)
            verdict = str(payload.get("verdict") or "INCONCLUSIVE").upper()
            payload["verdict"] = verdict if verdict in {"ALIGNED", "MISMATCH", "INCONCLUSIVE"} else "INCONCLUSIVE"
            for field in ("alignment_score", "confidence"):
                try:
                    payload[field] = max(0.0, min(1.0, float(payload.get(field) or 0)))
                except (TypeError, ValueError):
                    payload[field] = 0.0
            if isinstance(payload.get("content_entities"), list):
                payload["content_entities"] = [item for item in payload["content_entities"] if isinstance(item, str) and item.strip()][:12]
            for field, limit in (("title_claim", 300), ("content_summary", 600), ("visual_summary", 400), ("content_topic_label", 160), ("content_topic_type", 64), ("content_event_context", 160), ("mismatch_reason", 500)):
                if isinstance(payload.get(field), str):
                    payload[field] = payload[field][:limit]
            return ContentTruthFacts.model_validate(payload)
        except Exception as exc:
            raise RuntimeError("Content truth gateway returned invalid JSON") from exc
