import json
import subprocess
import tempfile
from pathlib import Path

from app.config import settings


class PeakFrameError(RuntimeError):
    pass


def classify_media_error(exc: PeakFrameError) -> str:
    message = str(exc).lower()
    if "requested format is not available" in message or "only images" in message:
        return "stream_unavailable"
    if "heatmap" in message or "most replayed" in message:
        return "heatmap_unavailable"
    if "timed out" in message:
        return "media_timeout"
    return "media_extraction_failed"


class PeakFrameExtractor:
    def extract(self, video_id: str, video_url: str) -> tuple[float, str]:
        """Download a passing Short once and save one frame at the heatmap peak."""
        with tempfile.TemporaryDirectory(prefix="ycgc-") as temp_dir:
            temp = Path(temp_dir)
            metadata, extractor_args = self._metadata(video_url)
            peak_seconds = self._peak_timestamp(metadata)
            source = temp / "source.mp4"
            self._download(video_url, source, extractor_args)
            output_dir = Path(settings.media_root) / "youtube-peaks"
            output_dir.mkdir(parents=True, exist_ok=True)
            output = output_dir / f"{video_id}.jpg"
            self._frame(source, peak_seconds, output)
            return peak_seconds, str(output)

    @staticmethod
    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(args, check=True, capture_output=True, text=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise PeakFrameError(str(exc)) from exc

    def _metadata(self, video_url: str) -> tuple[dict, list[str]]:
        # YouTube availability differs by delivery client. These are anonymous,
        # built-in yt-dlp clients; no cookies, account, keyword, or creator input.
        failures: list[str] = []
        for client in ("web_safari", "android", "tv"):
            args = ["--extractor-args", f"youtube:player_client={client}"]
            try:
                completed = self._run(["yt-dlp", "--skip-download", "--print-json", "--no-warnings", *args, video_url])
                metadata = json.loads(completed.stdout)
                formats = metadata.get("formats") if isinstance(metadata, dict) else None
                if isinstance(formats, list) and any(item.get("vcodec") not in (None, "none") for item in formats if isinstance(item, dict)):
                    return metadata, args
                failures.append(f"{client}: no playable video format")
            except (PeakFrameError, json.JSONDecodeError) as exc:
                failures.append(f"{client}: {str(exc)[:120]}")
        raise PeakFrameError("stream_unavailable; " + " | ".join(failures))

    @staticmethod
    def _peak_timestamp(metadata: dict) -> float:
        heatmap = metadata.get("heatmap")
        if not isinstance(heatmap, list) or not heatmap:
            raise PeakFrameError("Most Replayed heatmap is unavailable for this video")
        points = [point for point in heatmap if isinstance(point, dict) and isinstance(point.get("value"), (int, float))]
        if not points:
            raise PeakFrameError("Most Replayed heatmap contains no usable points")
        peak = max(points, key=lambda point: point["value"])
        start = float(peak.get("start_time", 0))
        end = float(peak.get("end_time", start))
        return max(0.0, (start + end) / 2)

    def _download(self, video_url: str, output: Path, extractor_args: list[str]) -> None:
        self._run(["yt-dlp", "--no-playlist", "-f", "best[height<=720]/best", "-o", str(output), "--no-warnings", *extractor_args, video_url])

    def _frame(self, source: Path, seconds: float, output: Path) -> None:
        self._run(["ffmpeg", "-y", "-ss", str(seconds), "-i", str(source), "-frames:v", "1", "-q:v", "2", str(output)])
