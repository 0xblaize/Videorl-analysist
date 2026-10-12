import shutil
import subprocess
from pathlib import Path

import cv2
import httpx

from videorl import config
from videorl.util import log, slugify


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def resolve_input(source: str, workdir: Path | None = None) -> Path:
    p = Path(source).expanduser()
    if p.exists():
        return p.resolve()
    if source.startswith("http://") or source.startswith("https://"):
        workdir = workdir or Path.cwd()
        dest = workdir / (slugify(source.split("/")[-1]) + ".mp4")
        log(f"downloading {source} -> {dest}")
        with httpx.stream("GET", source, timeout=600, follow_redirects=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        return dest.resolve()
    raise FileNotFoundError(f"video not found: {source}")


def probe(video: Path) -> dict:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = n / fps if fps > 0 and n > 0 else 0.0
    cap.release()
    has_audio = False
    try:
        ff = ffmpeg_exe()
        out = subprocess.run(
            [ff, "-i", str(video)],
            capture_output=True, text=True, timeout=60,
        ).stderr
        has_audio = "Audio:" in out
    except Exception:
        pass
    return {
        "path": str(video),
        "fps": fps,
        "frames": n,
        "width": w,
        "height": h,
        "duration_s": round(duration, 2),
        "size_mb": round(video.stat().st_size / 1e6, 1),
        "has_audio": has_audio,
    }


def usability(meta: dict) -> tuple[bool, str]:
    if meta["duration_s"] < 45:
        return False, f"too short ({meta['duration_s']:.0f}s)"
    cap = cv2.VideoCapture(meta["path"])
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return False, "cannot decode frames"
    if meta["size_mb"] < 0.5:
        return False, "file suspiciously small"
    return True, "plays, decodable, long enough for a >30s evidence question"


def fetch_meta(source: str) -> dict:
    video = resolve_input(source)
    meta = probe(video)
    meta["video_path"] = str(video)
    meta["name"] = video.name
    ok, note = usability(meta)
    meta["usable"] = ok
    meta["usability_note"] = note
    log(f"{video.name}: {meta['duration_s']:.0f}s, {meta['width']}x{meta['height']}, audio={meta['has_audio']}, usable={ok}")
    return meta
