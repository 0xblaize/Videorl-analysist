import json

from videorl.util import extract_json, log, ts
from videorl.vlm import VLMClient

PROMPT = """You are watching a film shot by shot. The contact sheet shows {n} frames, each labeled like "#12 3:40-3:52" (scene index, time range). Below is the dialogue transcript for roughly this stretch of the film.

For EVERY labeled scene on the sheet, output strict JSON (keep every field to max 12 words):
{{"scenes": [{{"idx": <int>, "setting": <where>, "people": <who>, "action": <what happens>, "objects": <notable props>, "visual_notes": <color, framing, mood>, "dialogue_context": <tie to nearby dialogue or empty>}}]}}

Describe only what the frames and transcript support. Notice recurring objects, costumes, locations, colors, and any deliberate visual motif. No speculation about meaning yet."""

PROMPT_NO_AUDIO = """You are watching a film shot by shot. The contact sheet shows {n} frames, each labeled like "#12 3:40-3:52" (scene index, time range).

For EVERY labeled scene on the sheet, output strict JSON (keep every field to max 12 words):
{{"scenes": [{{"idx": <int>, "setting": <where>, "people": <who>, "action": <what happens>, "objects": <notable props>, "visual_notes": <color, framing, mood>}}]}}

Describe only what the frames support. Notice recurring objects, costumes, locations, colors, and deliberate visual motifs."""


def _slice_transcript(segments: list[dict], start: float, end: float, max_chars: int = 1800) -> str:
    lines = []
    for s in segments:
        if s["end"] < start or s["start"] > end:
            continue
        lines.append(f"[{ts(s['start'])}] {s['text']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text or "(no dialogue in this stretch)"


def caption_sheets(
    client: VLMClient,
    sheets: list[dict],
    transcript_segments: list[dict],
    out_path,
) -> list[dict]:
    all_captions: list[dict] = []
    for i, sheet in enumerate(sheets, 1):
        prompt_t = PROMPT if transcript_segments else PROMPT_NO_AUDIO
        prompt = prompt_t.format(n=len(sheet["entries"]))
        if transcript_segments:
            prompt += "\n\nTranscript for this stretch:\n" + _slice_transcript(
                transcript_segments, sheet["start"], sheet["end"]
            )
        prompt += "\n\nScene labels on the sheet:\n" + "\n".join(e["label"] for e in sheet["entries"])
        log(f"captions sheet {i}/{len(sheets)} (scenes {sheet['scene_from']}-{sheet['scene_to']})")
        raw = client.chat(prompt, images=[sheet["sheet"]], max_tokens=1600, temperature=0.2, tag="captions")
        data = extract_json(raw)
        scenes = (data or {}).get("scenes", []) if isinstance(data, dict) else []
        if not isinstance(scenes, list):
            scenes = []
        for c in scenes:
            c["sheet"] = i
            all_captions.append(c)
        log(f"  got {len(scenes)} scene captions")
    from videorl.util import write_json

    write_json(out_path, all_captions)
    return all_captions


def outline_text(captions: list[dict], scenes: list[dict], max_scenes: int = 400) -> str:
    by_idx = {}
    for c in captions:
        try:
            by_idx[int(c.get("idx"))] = c
        except Exception:
            continue
    scene_by_idx = {s["idx"]: s for s in scenes}
    lines = []
    for idx in sorted(by_idx)[:max_scenes]:
        c = by_idx[idx]
        s = scene_by_idx.get(idx, {})
        span = f"{ts(s.get('start', 0))}-{ts(s.get('end', 0))}" if s else "?"
        lines.append(
            f"#{idx} [{span}] setting={c.get('setting','?')}; people={c.get('people','?')}; "
            f"action={c.get('action','?')}; objects={c.get('objects','')}; notes={c.get('visual_notes','')}"
        )
    return "\n".join(lines)


def transcript_highlights(segments: list[dict], every: int = 4, max_lines: int = 120) -> str:
    if not segments:
        return "(no transcript)"
    step = max(1, len(segments) // max_lines) or every
    lines = [f"[{ts(s['start'])}] {s['text']}" for s in segments[::step][:max_lines]]
    return "\n".join(lines)
