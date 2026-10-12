import re
import subprocess
from pathlib import Path

from videorl.ingest import ffmpeg_exe
from videorl.util import log


def detect_and_dump(video: Path, frames_dir: Path, duration: float, threshold: float = 0.27) -> tuple[list[dict], list[dict]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("cut_*.jpg"):
        old.unlink()
    cuts = _ffmpeg_cuts(video, threshold, frames_dir)
    if len(cuts) < 20 and duration > 300:
        log(f"only {len(cuts)} cuts at {threshold}; retrying at 0.15")
        cuts2 = _ffmpeg_cuts(video, 0.15, frames_dir)
        if len(cuts2) > len(cuts):
            cuts = cuts2
    cuts = _filter_close(cuts, min_gap=0.45)
    frames = sorted(frames_dir.glob("cut_*.jpg"))
    pairs = list(zip(cuts, frames))
    scenes = []
    dumped = []
    prev = 0.0
    for i, (t, fp) in enumerate(pairs):
        scenes.append({"idx": len(scenes), "start": round(prev, 2), "end": round(t, 2), "keyframe": str(fp)})
        dumped.append({"idx": len(scenes) - 1, "time": round(t, 2), "keyframe": str(fp)})
        prev = t
    scenes.append({"idx": len(scenes), "start": round(prev, 2), "end": round(duration, 2), "keyframe": None})
    scenes = [s for s in scenes if s["end"] - s["start"] >= 0.35]
    for i, s in enumerate(scenes):
        s["idx"] = i
    log(f"{len(scenes)} scenes from one ffmpeg pass ({len(pairs)} cut frames)")
    return scenes, dumped


def _ffmpeg_cuts(video: Path, threshold: float, frames_dir: Path) -> list[float]:
    ff = ffmpeg_exe()
    frames_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y", "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-fps_mode", "vfr", "-q:v", "4",
        str(frames_dir / "cut_%06d.jpg"),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    times = []
    assert proc.stderr is not None
    for line in proc.stderr:
        if "pts_time:" in line:
            m = re.search(r"pts_time:\s*([0-9.]+)", line)
            if m:
                times.append(float(m.group(1)))
    proc.wait()
    return times


def _filter_close(cuts: list[float], min_gap: float = 0.45) -> list[float]:
    out = []
    for t in cuts:
        if not out or t - out[-1] >= min_gap:
            out.append(t)
    return out


def speech_pause_candidates(transcript_segments: list[dict], min_pause: float = 2.0, duration: float = 0.0) -> list[float]:
    if not transcript_segments or duration <= 0:
        return []
    marks = []
    segs = sorted(transcript_segments, key=lambda s: s["start"])
    prev_end = 0.0
    for s in segs:
        gap = s["start"] - prev_end
        if gap >= min_pause and 1.0 < s["start"] < duration - 1.0:
            marks.append(round(s["start"], 2))
        prev_end = max(prev_end, s["end"])
    return marks


def refine_with_pauses(scenes: list[dict], pause_marks: list[float]) -> list[dict]:
    cuts = sorted({round(s["start"], 2) for s in scenes} | set(pause_marks))
    if not cuts or not scenes:
        return scenes
    duration = scenes[-1]["end"]
    out = []
    for i, start in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else duration
        if end - start >= 1.5:
            src = next((s for s in scenes if abs(s["start"] - start) < 0.05), None)
            out.append({"idx": len(out), "start": round(start, 2), "end": round(end, 2), "keyframe": src.get("keyframe") if src else None})
    return out or scenes
