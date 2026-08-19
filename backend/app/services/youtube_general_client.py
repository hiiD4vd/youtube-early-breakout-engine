"""Logged-out InnerTube access for broad YouTube video discovery.

This client is intentionally separate from the Shorts-only anonymous feed
adapter so the general-video lane can evolve without touching the seed
pipeline or the Shorts verifier.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings

YOUTUBE_ORIGIN = "https://www.youtube.com"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_COUNT_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_REQUEST_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}


class YoutubeGeneralDiscoveryError(RuntimeError):
    pass


class YoutubeGeneralInnertubeClient:
    """Best-effort general-video discovery from YouTube's public InnerTube surface."""

    def __init__(self, client: httpx.Client | None = None, region: str | None = None, language: str | None = None) -> None:
        self.region = region or settings.youtube_region
        self.language = language or settings.youtube_language
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.youtube_http_timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": f"{self.language},en;q=0.8",
                "Origin": YOUTUBE_ORIGIN,
            },
        )

    def close(self) -> None:
        self.client.close()

    def browse_trending(self, *, max_results: int | None = None) -> list[dict[str, Any]]:
        """Return general trending videos from the browse surface.

        We try the published trending browse page first and fall back to the
        explore feed if YouTube rotates the browse id.
        """
        max_results = max_results or settings.youtube_general_innertube_max_results
        html = self._get_bootstrap("/feed/trending")
        api_key, client_version, visitor_data = self._extract_innertube_config(html)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for browse_id in ("FEtrending", "FEexplore"):
            payload = self._browse(api_key, client_version, visitor_data, browse_id=browse_id)
            self._append_renderers(
                items,
                seen,
                self._extract_video_renderers(payload),
                source_surface="browse",
                max_results=max_results,
            )
            continuation = self._extract_continuation_token(payload)
            attempts = 0
            while continuation and len(items) < max_results and attempts < 6:
                payload = self._browse_continuation(api_key, client_version, visitor_data, continuation=continuation)
                self._append_renderers(
                    items,
                    seen,
                    self._extract_video_renderers(payload),
                    source_surface="browse",
                    max_results=max_results,
                )
                continuation = self._extract_continuation_token(payload)
                attempts += 1
            if items:
                return items[:max_results]
        return []

    def search_videos(self, query: str, *, max_results: int | None = None) -> list[dict[str, Any]]:
        """Return general-video search results from the InnerTube search surface."""
        query = query.strip()
        if not query:
            return []
        max_results = max_results or settings.youtube_general_innertube_max_results
        html = self._get_bootstrap("/results", params={"search_query": query})
        api_key, client_version, visitor_data = self._extract_innertube_config(html)
        payload = self._search(api_key, client_version, visitor_data, query=query)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        self._append_renderers(
            items,
            seen,
            self._extract_video_renderers(payload),
            source_surface="search",
            max_results=max_results,
            query=query,
        )
        continuation = self._extract_continuation_token(payload)
        attempts = 0
        while continuation and len(items) < max_results and attempts < 6:
            payload = self._search_continuation(api_key, client_version, visitor_data, continuation=continuation)
            self._append_renderers(
                items,
                seen,
                self._extract_video_renderers(payload),
                source_surface="search",
                max_results=max_results,
                query=query,
            )
            continuation = self._extract_continuation_token(payload)
            attempts += 1
        return items[:max_results]

    def _get_bootstrap(self, path: str, *, params: dict[str, str] | None = None) -> str:
        response = self._request(
            "GET",
            f"{YOUTUBE_ORIGIN}{path}",
            params={"hl": self.language, "gl": self.region, **(params or {})},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Retry transient DNS/connect failures without hiding the final cause.

        The Celery collector supplies a shared ``httpx.Client``. Previously
        that bypassed the browser-like headers configured by ``__init__`` and
        a single Docker DNS hiccup failed every region in the rotation.
        """
        headers = {**_REQUEST_HEADERS, "Accept-Language": f"{self.language},en;q=0.8", **kwargs.pop("headers", {})}
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                return self.client.request(method, url, headers=headers, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract_innertube_config(html: str) -> tuple[str, str, str | None]:
        def find(name: str) -> str | None:
            match = re.search(rf'"{name}":"([^"\\]+)"', html)
            return match.group(1) if match else None

        api_key = find("INNERTUBE_API_KEY")
        client_version = find("INNERTUBE_CLIENT_VERSION")
        visitor_data = find("VISITOR_DATA")
        if not api_key or not client_version:
            raise YoutubeGeneralDiscoveryError("Could not read Innertube configuration from a YouTube bootstrap page")
        return api_key, client_version, visitor_data

    def _browse(self, api_key: str, client_version: str, visitor_data: str | None, *, browse_id: str) -> dict[str, Any]:
        return self._post_innertube(
            api_key=api_key,
            client_version=client_version,
            visitor_data=visitor_data,
            endpoint="browse",
            body={
                "browseId": browse_id,
            },
        )

    def _search(self, api_key: str, client_version: str, visitor_data: str | None, *, query: str) -> dict[str, Any]:
        return self._post_innertube(
            api_key=api_key,
            client_version=client_version,
            visitor_data=visitor_data,
            endpoint="search",
            body={
                "query": query,
            },
        )

    def _browse_continuation(self, api_key: str, client_version: str, visitor_data: str | None, *, continuation: str) -> dict[str, Any]:
        return self._post_innertube(
            api_key=api_key,
            client_version=client_version,
            visitor_data=visitor_data,
            endpoint="browse",
            body={
                "continuation": continuation,
            },
        )

    def _search_continuation(self, api_key: str, client_version: str, visitor_data: str | None, *, continuation: str) -> dict[str, Any]:
        return self._post_innertube(
            api_key=api_key,
            client_version=client_version,
            visitor_data=visitor_data,
            endpoint="search",
            body={
                "continuation": continuation,
            },
        )

    def _post_innertube(
        self,
        *,
        api_key: str,
        client_version: str,
        visitor_data: str | None,
        endpoint: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        client: dict[str, Any] = {
            "clientName": "WEB",
            "clientVersion": client_version,
            "hl": self.language,
            "gl": self.region,
        }
        if visitor_data:
            client["visitorData"] = visitor_data
        response = self._request(
            "POST",
            f"{YOUTUBE_ORIGIN}/youtubei/v1/{endpoint}",
            params={"key": api_key, "prettyPrint": "false"},
            headers={
                "Origin": YOUTUBE_ORIGIN,
                "X-Youtube-Client-Name": "1",
                "X-Youtube-Client-Version": client_version,
            },
            json={"context": {"client": client}, **body},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise YoutubeGeneralDiscoveryError(f"InnerTube {endpoint} response was not JSON") from exc
        if not isinstance(payload, dict):
            raise YoutubeGeneralDiscoveryError(f"InnerTube {endpoint} response was not a JSON object")
        return payload

    @staticmethod
    def _walk_dicts(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from YoutubeGeneralInnertubeClient._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from YoutubeGeneralInnertubeClient._walk_dicts(child)

    def _extract_video_renderers(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        renderers: list[dict[str, Any]] = []
        seen: set[str] = set()
        # YouTube rotates renderer names between browse, search and compact
        # surfaces. Treat them as equivalent video evidence instead of
        # silently returning an empty list when the UI schema changes.
        renderer_names = ("videoRenderer", "gridVideoRenderer", "compactVideoRenderer", "playlistVideoRenderer")
        for node in self._walk_dicts(payload):
            for name in renderer_names:
                renderer = node.get(name)
                if not isinstance(renderer, dict):
                    continue
                video_id = renderer.get("videoId")
                if not isinstance(video_id, str) or not re.fullmatch(r"[\w-]{11}", video_id):
                    continue
                if video_id in seen:
                    continue
                seen.add(video_id)
                renderers.append(renderer)
        return renderers

    @staticmethod
    def _extract_continuation_token(payload: dict[str, Any]) -> str | None:
        """Best-effort extraction of the next continuation token from a payload."""
        for node in YoutubeGeneralInnertubeClient._walk_dicts(payload):
            for key in ("continuation", "continuationToken"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    return value
            continuation_command = node.get("continuationCommand")
            if isinstance(continuation_command, dict):
                value = continuation_command.get("token")
                if isinstance(value, str) and value:
                    return value
            next_data = node.get("nextContinuationData")
            if isinstance(next_data, dict):
                value = next_data.get("continuation")
                if isinstance(value, str) and value:
                    return value
        return None

    def _append_renderers(
        self,
        items: list[dict[str, Any]],
        seen: set[str],
        renderers: list[dict[str, Any]],
        *,
        source_surface: str,
        max_results: int,
        query: str | None = None,
    ) -> None:
        for renderer in renderers:
            video_id = renderer.get("videoId")
            if not isinstance(video_id, str) or video_id in seen:
                continue
            seen.add(video_id)
            items.append(
                self._normalize_renderer(
                    renderer,
                    source_surface=source_surface,
                    max_results=max_results,
                    query=query,
                )
            )
            if len(items) >= max_results:
                break

    @staticmethod
    def _first_text(node: dict[str, Any], names: tuple[str, ...]) -> str | None:
        for current in YoutubeGeneralInnertubeClient._walk_dicts(node):
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
    def _first_browse_id(node: dict[str, Any]) -> str | None:
        for current in YoutubeGeneralInnertubeClient._walk_dicts(node):
            browse_id = current.get("browseId")
            if isinstance(browse_id, str) and browse_id:
                return browse_id
        return None

    @staticmethod
    def _first_thumbnail_url(node: dict[str, Any]) -> str | None:
        for current in YoutubeGeneralInnertubeClient._walk_dicts(node):
            thumbnails = current.get("thumbnails")
            if isinstance(thumbnails, list):
                urls = [item.get("url") for item in thumbnails if isinstance(item, dict) and isinstance(item.get("url"), str)]
                if urls:
                    return urls[-1]
        return None

    @staticmethod
    def _parse_count(text: str | None) -> int | None:
        if not text:
            return None
        match = re.search(r"([\d,.]+)\s*([KMB])?", text.replace("views", "", 1), re.IGNORECASE)
        if not match:
            digits = re.sub(r"[^\d]", "", text)
            return int(digits) if digits else None
        number = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").upper()
        multiplier = _COUNT_SUFFIXES.get(suffix, 1)
        return int(number * multiplier)

    @staticmethod
    def _parse_duration(value: str | None) -> int:
        if not value:
            return 0
        match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", value)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    @staticmethod
    def _parse_relative_time(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.lower()
        now = datetime.now(UTC)
        match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text)
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "minute":
            return now - timedelta(minutes=amount)
        if unit == "hour":
            return now - timedelta(hours=amount)
        if unit == "day":
            return now - timedelta(days=amount)
        if unit == "week":
            return now - timedelta(weeks=amount)
        if unit == "month":
            return now - timedelta(days=amount * 30)
        if unit == "year":
            return now - timedelta(days=amount * 365)
        return None

    def _normalize_renderer(
        self,
        renderer: dict[str, Any],
        *,
        source_surface: str,
        max_results: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        title = self._first_text(renderer, ("title",))
        channel_title = self._first_text(renderer, ("shortBylineText", "ownerText", "longBylineText"))
        description = self._first_text(renderer, ("descriptionSnippet",))
        published_at = self._parse_relative_time(self._first_text(renderer, ("publishedTimeText", "timestampText")))
        duration_label = self._first_text(renderer, ("lengthText",))
        view_text = self._first_text(renderer, ("viewCountText", "shortViewCountText"))
        like_text = self._first_text(renderer, ("likeCount",))
        comment_text = self._first_text(renderer, ("commentCount",))
        return {
            "video_id": renderer.get("videoId"),
            "channel_id": self._first_browse_id(renderer),
            "channel_title": channel_title,
            "title": title,
            "description": description,
            "published_at": published_at,
            "thumbnail_url": self._first_thumbnail_url(renderer),
            "video_url": f"https://www.youtube.com/watch?v={renderer.get('videoId')}",
            "view_count": self._parse_count(view_text),
            "like_count": self._parse_count(like_text),
            "comment_count": self._parse_count(comment_text),
            "duration_label": duration_label,
            "duration_seconds": self._parse_duration(duration_label),
            "source_surface": source_surface,
            "query": query,
            "raw_payload": renderer,
            "max_results": max_results,
        }
