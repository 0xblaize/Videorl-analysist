import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from videorl import audio, frames, ingest, ocr, scenes as scenes_mod
from videorl.analysis import captions as captions_mod
from videorl.analysis import motifs as motifs_mod
from videorl.analysis import symbolic as symbolic_mod
from videorl.util import analysis_dir_for, log, read_json, ts, write_json
from videorl.vlm import VLMClient


def _fill_keyframes(video: Path, scenes: list[dict], max_seeks: int = 60) -> list[dict]:
    need = [s for s in scenes if not s.get("keyframe") or (s["end"] - s["start"]) > 30]
    cap = None
    filled = 0
    for s in need[:max_seeks]:
        if filled >= max_seeks:
            break
        if cap is None:
            cap = cv2.VideoCapture(str(video))
            if not cap.isOpened():
                break
        mid = (s["start"] + s["end"]) / 2.0
        cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        scale = min(1.0, 768 / max(1, w))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        dest = Path(video).parent / f"{video.stem}_analysis" / "keyframes" / f"mid_{s['idx']:04d}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        s["keyframe"] = str(dest)
        filled += 1
    if cap is not None:
        cap.release()
    if filled:
        log(f"filled {filled} mid-scene keyframes")
    return scenes


def run_stage1(source: str, workdir: Path | None = None) -> dict:
    meta = ingest.fetch_meta(source)
    video = Path(meta["video_path"])
    adir = analysis_dir_for(video)
    adir.mkdir(parents=True, exist_ok=True)
    if not meta["usable"]:
        write_json(adir / "meta.json", meta)
        raise SystemExit(f"video not usable: {meta['usability_note']}")
    write_json(adir / "meta.json", meta)
    duration = meta["duration_s"]

    audio_res = read_json(adir / "audio.json")
    audio_future = None
    executor = None
    if audio_res is None and meta["has_audio"]:
        executor = ThreadPoolExecutor(max_workers=1)
        log("starting audio extraction + transcription in background...")
        audio_future = executor.submit(audio.run, video, adir)

    scenes = read_json(adir / "scenes.json")
    if scenes is None:
        log("one ffmpeg pass: scene cuts + keyframe dump...")
        scenes, _ = scenes_mod.detect_and_dump(video, adir / "keyframes", duration)
        scenes = _fill_keyframes(video, scenes)
        write_json(adir / "scenes.json", scenes)

    if audio_future is not None:
        log("waiting for transcription to finish...")
        audio_res = audio_future.result()
        write_json(adir / "audio.json", audio_res)
        executor.shutdown(wait=False)
    if audio_res is None:
        audio_res = {"segments": [], "swells": []}
    segments = audio_res.get("segments", [])
    swells = audio_res.get("swells", [])

    scenes = read_json(adir / "scenes.json")
    sheets = read_json(adir / "sheets.json")
    if sheets is None:
        per = max(24, ((len(scenes) + 5) // 6))
        per = (per + 3) // 4 * 4
        sheets = frames.make_contact_sheets(scenes, adir / "contact_sheets", per_sheet=per)
        write_json(adir / "sheets.json", sheets)

    ocr_texts = read_json(adir / "ocr.json")
    if ocr_texts is None:
        ocr_texts = ocr.ocr_keyframes(scenes)
        write_json(adir / "ocr.json", ocr_texts)

    client = VLMClient()
    if not client.available():
        raise SystemExit("no API key set — put your keys in ~/.videorl_providers.json (openrouter and/or groq)")

    captions = read_json(adir / "captions.json")
    if captions is None:
        captions = captions_mod.caption_sheets(client, sheets, segments, adir / "captions.json")

    motifs = read_json(adir / "motifs.json")
    if motifs is None:
        motifs = motifs_mod.run(client, scenes, captions, segments)
        write_json(adir / "motifs.json", motifs)

    candidates = read_json(adir / "symbolic_candidates.json")
    if candidates is None:
        outline = captions_mod.outline_text(captions, scenes)
        candidates = symbolic_mod.run(client, video, adir, outline, segments, motifs, swells, duration)
        write_json(adir / "symbolic_candidates.json", candidates)

    report = write_stage1_report(meta, scenes, segments, motifs, candidates, adir)
    log(f"stage 1 done -> {adir / 'stage1_report.md'}")
    return {"meta": meta, "adir": adir, "candidates": candidates, "report": report}


def write_stage1_report(meta, scenes, segments, motifs, candidates, adir: Path) -> str:
    lines = []
    lines.append(f"# Stage 1 analysis — {meta['name']}")
    lines.append("")
    lines.append(f"- Length: {ts(meta['duration_s'])} ({meta['duration_s']:.0f} s), {meta['width']}x{meta['height']}, audio={meta['has_audio']}")
    lines.append(f"- Usable: {'YES' if meta['usable'] else 'NO'} — {meta['usability_note']}")
    lines.append(f"- Scenes detected: {len(scenes)}")
    lines.append(f"- Transcript segments: {len(segments)}")
    lines.append("")
    lines.append("## Recurring motifs (deliberate, VLM-verified)")
    for m in motifs.get("verified_deliberate", [])[:8]:
        lines.append(
            f"- **{m.get('motif')}** — {m.get('scene_count', '?')} scenes, {ts(m.get('first_seen', 0))}→{ts(m.get('last_seen', 0))}: {m.get('why_deliberate', '')} (hint: {m.get('likely_meaning_hint', '')})"
        )
    if not motifs.get("verified_deliberate"):
        lines.append("- (none verified)")
    lines.append("")
    lines.append("## Readings the film supports (verified against the footage)")
    ordered = sorted(
        candidates,
        key=lambda c: (1 if (c.get("verification") or {}).get("supported") else 0, float(c.get("confidence") or 0)),
        reverse=True,
    )
    for c in ordered:
        v = c.get("verification", {})
        ok = "PASS" if v.get("supported") else "FAIL"
        lines.append("")
        lines.append(f"### Reading {c.get('id')}: {c.get('symbol')} — check {ok}")
        lines.append(f"- Answer: {c.get('meaning')}")
        lines.append(f"- Question to ask: {c.get('question_angle')}")
        lines.append("- Evidence:")
        total = 0.0
        for r in c.get("evidence_ranges", []):
            dur = float(r["end"]) - float(r["start"])
            total += dur
            lines.append(f"  - {ts(float(r['start']))}-{ts(float(r['end']))} ({dur:.0f} s, {r.get('what_shows', '')})")
        lines.append(f"- Total evidence: {total:.0f} s")
        lines.append(f"- Wrong reading to beat: {c.get('rival_reading', '')}")
        lines.append(f"- Why it loses: {c.get('why_rival_weaker', '')}")
        lines.append(f"- Check note: {v.get('overall', v.get('note', ''))}")
        lines.append(f"- Confidence: {c.get('confidence')}")
    if ordered:
        best = ordered[0]
        lines.append("")
        lines.append("## Next step")
        lines.append(f"Run: videorl question <video>. It uses the top reading: {best.get('symbol')}.")
    report = "\n".join(lines)
    (adir / "stage1_report.md").write_text(report, encoding="utf-8")
    return report
