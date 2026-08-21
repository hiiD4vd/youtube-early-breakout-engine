import re
import subprocess
import tempfile
from pathlib import Path

from app.config import settings


def _ytdlp_cookie_args() -> list[str]:
    """Return ``--cookies`` args when a cookies file is configured and present.

    Used for Shorts *verification* (metadata) only. Caption/transcript fetch
    must stay cookie-less, so this helper is intentionally not called there.
    """
    path = getattr(settings, "yt_dlp_cookies_path", "") or ""
    if path and Path(path).is_file():
        return ["--cookies", path]
    return []


def fetch_transcript(video_url: str, timeout: int = 120) -> str | None:
    # NOTE: do NOT pass --cookies here. A logged-in YouTube session makes the
    # player return a different response and yt-dlp falls back to the "tv"
    # player client, which fails with "The page needs to be reloaded". Anonymous
    # (cookie-less) caption fetch works reliably.
    with tempfile.TemporaryDirectory(prefix="ycgc-caption-") as directory:
        template = str(Path(directory) / "caption.%(ext)s")
        result = subprocess.run(
            [
                "yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
                "--sub-langs", "all", "-o", template, "--no-warnings", video_url,
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return None
        files = list(Path(directory).glob("*.vtt"))
        if not files:
            return None
        best = max(files, key=lambda path: path.stat().st_size)
        lines = [line.strip() for line in best.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip() and "-->" not in line and line.strip() != "WEBVTT"]
        return re.sub(r"<[^>]+>", "", " ".join(lines))[:20_000] or None
