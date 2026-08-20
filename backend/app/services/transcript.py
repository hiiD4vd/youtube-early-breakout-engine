import re
import subprocess
import tempfile
from pathlib import Path


def fetch_transcript(video_url: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="ycgc-caption-") as directory:
        template = str(Path(directory) / "caption.%(ext)s")
        result = subprocess.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "all", "-o", template, "--no-warnings", video_url], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return None
        files = list(Path(directory).glob("*.vtt"))
        if not files:
            return None
        best = max(files, key=lambda path: path.stat().st_size)
        lines = [line.strip() for line in best.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip() and "-->" not in line and line.strip() != "WEBVTT"]
        return re.sub(r"<[^>]+>", "", " ".join(lines))[:20_000] or None
