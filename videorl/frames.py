import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from videorl import config
from videorl.util import log, ts


def extract_keyframes(video: Path, scenes: list[dict], out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    results = []
    for s in scenes:
        mid = (s["start"] + s["end"]) / 2.0
        frame_no = int(mid * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_no))
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
            ok, frame = cap.read()
        if not ok:
            results.append({**s, "keyframe": None})
            continue
        h, w = frame.shape[:2]
        scale = min(1.0, config.FRAME_MAX_W / max(1, w))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        name = f"scene_{s['idx']:04d}_{ts(s['start']).replace(':', '-')}.jpg"
        path = out_dir / name
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
        results.append({**s, "keyframe": str(path)})
    cap.release()
    log(f"extracted {sum(1 for r in results if r.get('keyframe'))} keyframes")
    return results


def make_contact_sheets(scenes: list[dict], out_dir: Path, per_sheet: int = 16) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    cell_w, cell_h = 320, 200
    for b0 in range(0, len(scenes), per_sheet):
        block = scenes[b0 : b0 + per_sheet]
        cols = 4
        rows = (len(block) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 18, 18))
        draw = ImageDraw.Draw(sheet)
        entries = []
        for i, s in enumerate(block):
            if not s.get("keyframe"):
                continue
            img = Image.open(s["keyframe"]).convert("RGB")
            img.thumbnail((cell_w - 4, cell_h - 22))
            x = (i % cols) * cell_w + 2
            y = (i // cols) * cell_h + 20
            sheet.paste(img, (x, y))
            label = f"#{s['idx']} {ts(s['start'])}-{ts(s['end'])}"
            draw.text((x + 2, (i // cols) * cell_h + 4), label, fill=(255, 230, 120))
            entries.append({"scene_idx": s["idx"], "start": s["start"], "end": s["end"], "label": label})
        if not entries:
            continue
        path = out_dir / f"sheet_{b0:04d}_{(b0 + len(block) - 1):04d}.jpg"
        sheet.save(path, quality=config.JPEG_QUALITY)
        sheets.append(
            {
                "sheet": str(path),
                "scene_from": block[0]["idx"],
                "scene_to": block[-1]["idx"],
                "start": block[0]["start"],
                "end": block[-1]["end"],
                "entries": entries,
            }
        )
    log(f"{len(sheets)} contact sheets")
    return sheets


def frames_at(video: Path, times: list[float], out_dir: Path, tag: str = "ev") -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    ff = None
    for i, t in enumerate(times):
        dest = out_dir / f"{tag}_{i:02d}_{ts(t).replace(':', '-')}.jpg"
        if ff is None:
            from videorl.ingest import ffmpeg_exe

            ff = ffmpeg_exe()
        subprocess.run(
            [ff, "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, timeout=120,
        )
        if dest.exists():
            img = Image.open(dest).convert("RGB")
            img.thumbnail((config.FRAME_MAX_W, config.FRAME_MAX_W))
            img.save(dest, quality=config.JPEG_QUALITY)
            paths.append(str(dest))
    return paths


def sheet_from_frames(frame_paths: list[str], out_path: Path, labels: list[str] | None = None, cols: int = 4) -> str | None:
    imgs = [Image.open(p).convert("RGB") for p in frame_paths if Path(p).exists()]
    if not imgs:
        return None
    cell_w, cell_h = 340, 230
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, img in enumerate(imgs):
        img.thumbnail((cell_w - 6, cell_h - 26))
        x = (i % cols) * cell_w + 3
        y = (i // cols) * cell_h + 22
        sheet.paste(img, (x, y))
        label = labels[i] if labels and i < len(labels) else f"{i + 1}"
        draw.text((x + 2, (i // cols) * cell_h + 4), label[:48], fill=(255, 220, 100))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=config.JPEG_QUALITY)
    return str(out_path)
