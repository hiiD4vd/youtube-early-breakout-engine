"""Anonymous, HTTP-only access to the Shorts distribution surface."""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings
from app.schemas.youtube import SeedDiscoveryResult, YoutubeSeed

YOUTUBE_ORIGIN = "https://www.youtube.com"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class YoutubeDiscoveryError(RuntimeError):
    pass


class YoutubeAnonymousClient:
    """Defensive adapter for an undocumented, logged-out Innertube surface."""

    def __init__(self, client: httpx.Client | None = None, region: str | None = None, language: str | None = None) -> None:
        self.region = region or settings.youtube_region
        self.language = language or settings.youtube_language
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.youtube_http_timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": f"{self.language},en;q=0.8", "Origin": YOUTUBE_ORIGIN},
        )

    def close(self) -> None:
        self.client.close()

    def discover_seeds(
        self,
        *,
        max_pages: int | None = None,
        max_accepted: int | None = None,
        max_age_hours: int | None = None,
        require_exact_views: bool = True,
    ) -> tuple[list[YoutubeSeed], SeedDiscoveryResult]:
        max_pages = max_pages or settings.youtube_seed_pages_per_run
        max_accepted = max_accepted or settings.youtube_seed_limit_per_run
        html = self._get_shorts_bootstrap()
        api_key, client_version, visitor_data = self._extract_innertube_config(html)
        initial_ids = self._extract_reel_video_ids(html)
        if not initial_ids:
            raise YoutubeDiscoveryError("Anonymous Shorts bootstrap did not expose a reel video ID")

        result = SeedDiscoveryResult(bootstrap_video_id=random.choice(initial_ids))
        seen_ids: set[str] = set()
        accepted: dict[str, YoutubeSeed] = {}
        metadata_cache: dict[str, dict[str, Any]] = {}
        queue = [result.bootstrap_video_id, *initial_ids]
        while queue and result.pages_requested < max_pages:
            video_id = queue.pop(0)
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            payload = self._fetch_reel_item(api_key, client_version, visitor_data, video_id)
            result.pages_requested += 1
            for candidate in self._extract_candidates(payload):
                candidate_id = candidate.get("video_id")
                if candidate_id and candidate_id not in seen_ids:
                    queue.append(candidate_id)
                result.candidates_seen += 1
                if candidate_id:
                    if candidate_id not in metadata_cache:
                        metadata_cache[candidate_id] = self._fetch_player_metadata(
                            api_key, client_version, visitor_data, candidate_id
                        )
                    candidate.update(metadata_cache[candidate_id])
                seed, reason = self._to_seed(candidate, max_age_hours=max_age_hours)
                if seed and require_exact_views and seed.view_count_precision != "exact":
                    # Rounded feed text ("1.2K views") as a velocity baseline is
                    # noise up to ~100 views; our tiers start at 250/h. Reject.
                    seed, reason = None, "imprecise_view_count"
                if reason == "too_old":
                    result.seeds_rejected_age += 1
                elif reason:
                    result.seeds_rejected_incomplete += 1
                elif seed:
                    accepted.setdefault(seed.video_id, seed)
                    if len(accepted) >= max_accepted:
                        break
            if len(accepted) >= max_accepted:
                break
        result.seeds_written = len(accepted)
        return list(accepted.values()), result

    def fetch_current_metadata(self, video_id: str) -> dict[str, Any]:
        """Fetch current public metadata for an already anonymous-feed-discovered ID."""
        html = self._get_shorts_bootstrap()
        api_key, client_version, visitor_data = self._extract_innertube_config(html)
        return self._fetch_player_metadata(api_key, client_version, visitor_data, video_id)

    def _get_shorts_bootstrap(self) -> str:
        response = self.client.get(f"{YOUTUBE_ORIGIN}/shorts", params={"hl": self.language, "gl": self.region})
        response.raise_for_status()
        return response.text

    @staticmethod
    def _extract_innertube_config(html: str) -> tuple[str, str, str | None]:
        def find(name: str) -> str | None:
            match = re.search(rf'"{name}":"([^"\\]+)"', html)
            return match.group(1) if match else None
        api_key, client_version, visitor_data = find("INNERTUBE_API_KEY"), find("INNERTUBE_CLIENT_VERSION"), find("VISITOR_DATA")
        if not api_key or not client_version:
            raise YoutubeDiscoveryError("Could not read Innertube configuration from anonymous Shorts page")
        return api_key, client_version, visitor_data

    @staticmethod
    def _extract_reel_video_ids(text: str) -> list[str]:
        ids = re.findall(r'"reelWatchEndpoint":\{[^{}]*?"videoId":"([\w-]{11})"', text)
        return list(dict.fromkeys(ids))

    def _fetch_reel_item(self, api_key: str, client_version: str, visitor_data: str | None, video_id: str) -> dict[str, Any]:
        client: dict[str, Any] = {"clientName": "WEB", "clientVersion": client_version, "hl": self.language, "gl": self.region}
        if visitor_data:
            client["visitorData"] = visitor_data
        response = self.client.post(
            f"{YOUTUBE_ORIGIN}/youtubei/v1/reel/reel_item_watch",
            params={"key": api_key, "prettyPrint": "false"},
            headers={"X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": client_version},
            json={
                "context": {"client": client},
                "playerRequest": {"videoId": video_id},
                # Current web Shorts reel requests require this opaque reel mode
                # marker. It is not a content topic, account credential, or seed.
                "params": "CA8qADAC",
                "disablePlayerResponse": True,
            },
        )
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise YoutubeDiscoveryError("Innertube reel response was not JSON") from exc

    def _fetch_player_metadata(
        self, api_key: str, client_version: str, visitor_data: str | None, video_id: str
    ) -> dict[str, Any]:
        """Hydrate an anonymously discovered feed item with non-topic metadata."""
        client: dict[str, Any] = {
            "clientName": "WEB",
            "clientVersion": client_version,
            "hl": self.language,
            "gl": self.region,
        }
        if visitor_data:
            client["visitorData"] = visitor_data
        response = self.client.post(
            f"{YOUTUBE_ORIGIN}/youtubei/v1/player",
            params={"key": api_key, "prettyPrint": "false"},
            headers={"X-Youtube-Client-Name": "1", "X-Youtube-Client-Version": client_version},
            json={
                "context": {"client": client},
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        details = payload.get("videoDetails") if isinstance(payload, dict) else None
        if not isinstance(details, dict):
            return {}
        microformat = payload.get("microformat", {}).get("playerMicroformatRenderer", {})
        if not isinstance(microformat, dict):
            microformat = {}
        thumbnails = details.get("thumbnail", {}).get("thumbnails", [])
        thumbnail_url = None
        if isinstance(thumbnails, list):
            urls = [item.get("url") for item in thumbnails if isinstance(item, dict) and isinstance(item.get("url"), str)]
            thumbnail_url = urls[-1] if urls else None
        return {
            "channel_id": details.get("channelId"),
            "channel_title": details.get("author"),
            "title": details.get("title"),
            "view_count": self._parse_view_count(details.get("viewCount")),
            "view_count_precision": "exact",
            "published_at": self._parse_published_date(
                microformat.get("uploadDate") or microformat.get("publishDate")
            ),
            "published_at_precision": "day",
            "thumbnail_url": thumbnail_url,
        }

    def _extract_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for node in self._walk_dicts(payload):
            video_id = node.get("videoId")
            if not isinstance(video_id, str) or not re.fullmatch(r"[\w-]{11}", video_id):
                continue
            view_text = self._first_text(node, ("shortViewCountText", "viewCountText", "viewCount"))
            candidates.append({
                "video_id": video_id,
                "channel_id": self._first_channel_id(node),
                "channel_title": self._first_text(node, ("shortBylineText", "ownerText", "longBylineText")),
                "title": self._first_text(node, ("headline", "title")),
                "view_count": self._parse_view_count(view_text),
                "view_count_precision": "rounded" if self._is_compact_count(view_text) else "exact",
                "published_at": self._parse_relative_time(self._first_text(node, ("publishedTimeText", "timestampText"))),
                "published_at_precision": "hour",
                "thumbnail_url": self._first_thumbnail_url(node),
            })
        return candidates

    @staticmethod
    def _walk_dicts(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from YoutubeAnonymousClient._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from YoutubeAnonymousClient._walk_dicts(child)

    @staticmethod
    def _first_text(node: dict[str, Any], names: tuple[str, ...]) -> str | None:
        for current in YoutubeAnonymousClient._walk_dicts(node):
            for name in names:
                value = current.get(name)
                if isinstance(value, dict):
                    if isinstance(value.get("simpleText"), str):
                        return value["simpleText"]
                    runs = value.get("runs")
                    if isinstance(runs, list):
                        text = "".join(str(item.get("text", "")) for item in runs if isinstance(item, dict))
                        if text:
                            return text
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def _first_channel_id(node: dict[str, Any]) -> str | None:
        for current in YoutubeAnonymousClient._walk_dicts(node):
            browse_id = current.get("browseId")
            if isinstance(browse_id, str) and browse_id.startswith("UC"):
                return browse_id
        return None

    @staticmethod
    def _first_thumbnail_url(node: dict[str, Any]) -> str | None:
        for current in YoutubeAnonymousClient._walk_dicts(node):
            thumbnails = current.get("thumbnails")
            if isinstance(thumbnails, list):
                urls = [item.get("url") for item in thumbnails if isinstance(item, dict) and isinstance(item.get("url"), str)]
                if urls:
                    return urls[-1]
        return None

    @staticmethod
    def _is_compact_count(value: str | None) -> bool:
        """True when the source text uses a compact suffix (1.2K) = rounded."""
        if not value:
            return False
        normalized = value.lower().replace(",", "").replace("views", "").replace("view", "").strip()
        return bool(re.search(r"\d\s*[kmb]\b", normalized))

    @staticmethod
    def _parse_view_count(value: str | None) -> int | None:
        if not value:
            return None
        normalized = value.lower().replace(",", "").replace("views", "").replace("view", "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)\s*([kmb])?", normalized)
        if not match:
            return None
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2) or "", 1)
        return int(float(match.group(1)) * multiplier)

    @staticmethod
    def _parse_relative_time(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.lower().replace("streamed", "").strip()
        now = datetime.now(UTC)
        if "yesterday" in normalized:
            return now - timedelta(days=1)
        match = re.search(r"(\d+)\s+(second|minute|hour|day|week)s?\s+ago", normalized)
        if not match:
            return None
        count, unit = int(match.group(1)), match.group(2)
        return now - {"second": timedelta(seconds=count), "minute": timedelta(minutes=count), "hour": timedelta(hours=count), "day": timedelta(days=count), "week": timedelta(weeks=count)}[unit]

    @staticmethod
    def _parse_published_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    @staticmethod
    def _to_seed(candidate: dict[str, Any], *, max_age_hours: int | None = None) -> tuple[YoutubeSeed | None, str | None]:
        video_id, channel_id = candidate.get("video_id"), candidate.get("channel_id")
        view_count, published_at = candidate.get("view_count"), candidate.get("published_at")
        if not video_id or not channel_id or view_count is None or not published_at:
            return None, "incomplete"
        permitted_age = max_age_hours if max_age_hours is not None else settings.youtube_seed_max_age_hours
        if datetime.now(UTC) - published_at > timedelta(hours=permitted_age):
            return None, "too_old"
        return YoutubeSeed(video_id=video_id, channel_id=channel_id, channel_title=candidate.get("channel_title"), title=candidate.get("title"), seed_view_count=view_count, published_at=published_at, seeded_at=datetime.now(UTC), video_url=f"{YOUTUBE_ORIGIN}/shorts/{video_id}", thumbnail_url=candidate.get("thumbnail_url"), view_count_precision=candidate.get("view_count_precision", "exact"), published_at_precision=candidate.get("published_at_precision", "hour")), None
