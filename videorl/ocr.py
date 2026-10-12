import shutil
import subprocess
from pathlib import Path

from videorl.util import log


def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(path: Path) -> str:
    if not available():
        return ""
    try:
        out = subprocess.run(
            ["tesseract", str(path), "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=60,
        )
        text = out.stdout.strip()
        if len(text.split()) < 3:
            return ""
        return " ".join(text.split())
    except Exception:
        return ""


def ocr_keyframes(scenes: list[dict]) -> dict[str, str]:
    if not available():
        log("tesseract not installed; OCR skipped")
        return {}
    texts = {}
    for s in scenes:
        kf = s.get("keyframe")
        if not kf:
            continue
        t = ocr_image(Path(kf))
        if t:
            texts[str(s["idx"])] = t
    if texts:
        log(f"OCR found text in {len(texts)} keyframes")
    return texts
