import subprocess
import wave
from pathlib import Path

import numpy as np

from videorl import config
from videorl.ingest import ffmpeg_exe
from videorl.util import log


def extract_wav(video: Path, out_wav: Path, sample_rate: int = 16000) -> Path | None:
    if out_wav.exists() and out_wav.stat().st_size > 10000:
        return out_wav
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_exe()
    r = subprocess.run(
        [ff, "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "wav", str(out_wav)],
        capture_output=True, timeout=600,
    )
    if r.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        log("audio extraction failed or no audio track")
        return None
    return out_wav


def _load_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def energy_map(wav_path: Path, hop: float = 1.0) -> list[dict]:
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop_n = int(sr * hop)
    points = []
    for i in range(0, len(audio), hop_n):
        chunk = audio[i : i + hop_n]
        if len(chunk) == 0:
            break
        rms = float(np.sqrt(np.mean(chunk**2)))
        points.append({"t": round(i / sr, 2), "rms": round(rms, 5)})
    return points


def swells(points: list[dict], z: float = 1.8) -> list[dict]:
    if len(points) < 8:
        return []
    vals = np.array([p["rms"] for p in points])
    mu, sd = float(vals.mean()), float(vals.std() + 1e-9)
    out = []
    run = []
    for p, v in zip(points, vals):
        if (v - mu) / sd >= z:
            run.append(p)
        else:
            if len(run) >= 4:
                out.append({"start": run[0]["t"], "end": run[-1]["t"], "peak_rms": round(max(x["rms"] for x in run), 4)})
            run = []
    if len(run) >= 4:
        out.append({"start": run[0]["t"], "end": run[-1]["t"], "peak_rms": round(max(x["rms"] for x in run), 4)})
    return out


def transcribe(wav_path: Path) -> list[dict]:
    from faster_whisper import WhisperModel

    log(f"transcribing with faster-whisper ({config.WHISPER_MODEL}, int8, cpu)...")
    model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    audio_arr, sr = _load_wav(wav_path)
    if sr != 16000:
        idx = np.linspace(0, len(audio_arr) - 1, int(len(audio_arr) * 16000 / sr)).astype(np.int64)
        audio_arr = audio_arr[idx]
    segments, info = model.transcribe(
        audio_arr,
        vad_filter=True,
        beam_size=1,
        language="en",
        condition_on_previous_text=False,
    )
    out = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        if len(out) % 20 == 0:
            log(f"  transcript at {int(seg.end // 60)}:{int(seg.end % 60):02d} of film...")
    log(f"transcript: {len(out)} segments, language={info.language}")
    return out


def write_srt(segments: list[dict], path: Path) -> None:
    def fmt(t: float) -> str:
        ms = int(round((t - int(t)) * 1000))
        s = int(t) % 60
        m = (int(t) // 60) % 60
        h = int(t) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{fmt(seg['start'])} --> {fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run(video: Path, analysis_dir: Path) -> dict:
    wav = extract_wav(video, analysis_dir / "audio.wav")
    if wav is None:
        return {"segments": [], "energy": [], "swells": []}
    segments = transcribe(wav)
    write_srt(segments, analysis_dir / "transcript.srt")
    energy = energy_map(wav)
    sw = swells(energy)
    return {"segments": segments, "energy": energy, "swells": sw}
