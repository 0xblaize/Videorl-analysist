from pathlib import Path

from videorl.analysis.captions import transcript_highlights
from videorl.frames import frames_at, sheet_from_frames
from videorl.util import extract_json, log, read_json, ts, write_json

CATEGORY_GUIDE = """Category: Symbolic / Metaphorical Interpretation.
It covers: what an object, character, filmmaking device or ending stands for in an authored film.
Question types: what an object/animal/image symbolizes; what a character or their transformation represents; what a filmmaking device conveys (color, music, framing, contrast); what the ending or climax signifies; the metaphor or theme of the whole film.
An answer looks like: one clause stating the meaning — "It represents the son's anxiety about living up to his father's legacy."
"Clearly best" means you can name what makes every other reading weaker: something in the film contradicts it, it rests on one detail where your answer rests on several, or the film never connects it to the subject of the question.
Two readings the film supports about equally are an ambiguous question, not an answer plus a distractor.
Music, dialogue and sound design are in scope — but only what the film establishes.
Not a symbol anyone can guess without watching, and not a meaning that rests on one shot."""

PROPOSE_PROMPT = """You are an expert film analyst. Using ONLY the scene outline, transcript highlights and verified motifs below, propose 3-5 candidate symbolic/metaphorical readings for this film.

{guide}

Film outline (scene index, time range, description):
{outline}

Transcript highlights:
{transcript}

Verified deliberate motifs:
{motifs}

Notable music/audio swells:
{swells}

Output STRICT JSON:
{{"candidates": [
  {{
    "id": 1,
    "symbol": "<the object/character/device/ending>",
    "meaning": "<one clause, starts with 'It represents' or 'It symbolizes' — this will be the correct answer text>",
    "question_angle": "<what a hard question about this could ask, phrased WITHOUT any time codes or position words>",
    "evidence_ranges": [
      {{"start": <seconds float>, "end": <seconds float>, "what_shows": "<concrete visible/audible content>"}}
    ],
    "counterevidence": "<what in the film weakens other readings or could challenge this one>",
    "rival_reading": "<the strongest competing interpretation, one clause>",
    "why_rival_weaker": "<one clause naming the concrete reason the rival loses>",
    "confidence": <0.0-1.0>
  }}
]}}

Hard constraints (violating any makes the candidate useless):
- evidence_ranges must be REAL moments from the outline above; each range 4-90 seconds; at least 2 distinct ranges; total duration across ranges > 30 seconds.
- The meaning must rest on evidence spread across the film, not one shot.
- No two candidates may express the same meaning.
- question_angle must be answerable from the video alone, self-contained, with no time codes or position words like "near the end" / "at the start".
Return JSON only."""

VERIFY_PROMPT = """You are fact-checking a symbolic reading of a film against its actual footage. The attached contact sheet contains frames from the evidence ranges cited for the claim. Each cell is labeled with the range it came from.

Claim: {symbol} — "{meaning}"
Evidence described as: {evidence}

For EACH labeled group of frames, answer: does the footage show what the evidence description says?
Output STRICT JSON:
{{"supported": <true|false>, "per_range": [{{"range": "<label>", "matches": <true|false>, "note": "<what the frames actually show>"}}], "overall": "<one sentence: is the claim well supported by these frames?>"}}
JSON only."""


def validate_candidate(c: dict, duration: float) -> list[str]:
    problems = []
    ranges = c.get("evidence_ranges") or []
    if len(ranges) < 2:
        problems.append("needs at least 2 evidence ranges")
    total = 0.0
    for r in ranges:
        try:
            s, e = float(r["start"]), float(r["end"])
        except Exception:
            problems.append(f"bad range {r}")
            continue
        if e <= s:
            problems.append(f"range end<=start: {r}")
        if e - s > 95:
            problems.append(f"range too long (>95s): {r}")
        if s < 0 or e > duration + 2:
            problems.append(f"range outside video: {r}")
        total += max(0.0, e - s)
    if total <= 30:
        problems.append(f"total evidence {total:.0f}s <= 30s")
    if not c.get("meaning") or len(str(c.get("meaning", ""))) < 15:
        problems.append("meaning too short")
    return problems


def propose(client, outline: str, segments: list, motifs: dict, swells: list, duration: float) -> list[dict]:
    prompt = PROPOSE_PROMPT.format(
        guide=CATEGORY_GUIDE,
        outline=outline or "(no captions yet)",
        transcript=transcript_highlights(segments),
        motifs=str(motifs.get("verified_deliberate", []))[:2000],
        swells=str(swells)[:600] or "(none detected)",
    )
    log("proposing symbolic candidates...")
    raw = client.chat(prompt, max_tokens=4000, temperature=0.5, tag="symbolic-propose")
    data = extract_json(raw)
    cands = (data or {}).get("candidates", []) if isinstance(data, dict) else []
    out = []
    for c in cands if isinstance(cands, list) else []:
        problems = validate_candidate(c, duration)
        if problems:
            log(f"  dropping candidate {c.get('id')}: {'; '.join(problems)}")
            continue
        out.append(c)
    log(f"{len(out)} valid symbolic candidates")
    return out


def verify_candidate(client, video: Path, candidate: dict, workdir: Path, duration: float) -> dict:
    ranges = candidate.get("evidence_ranges", [])
    frame_paths = []
    labels = []
    for i, r in enumerate(ranges):
        s, e = float(r["start"]), float(r["end"])
        n = 3 if e - s > 25 else 2
        times = [s + (e - s) * (k + 1) / (n + 1) for k in range(n)]
        got = frames_at(video, times, workdir / "verify_frames", tag=f"cand{candidate.get('id', 0)}_r{i}")
        frame_paths.extend(got)
        labels.extend([f"range{i + 1}: {ts(s)}-{ts(e)}"] * len(got))
    if not frame_paths:
        return {"supported": None, "note": "no frames extracted"}
    sheet = sheet_from_frames(
        frame_paths,
        workdir / "verify" / f"candidate_{candidate.get('id', 0)}.jpg",
        labels=[f"{labels[i]} ({i % 3 + 1}/{sum(1 for l in labels if l == labels[i])})" for i in range(len(labels))],
    )
    evidence = "; ".join(f"{ts(float(r['start']))}-{ts(float(r['end']))}: {r.get('what_shows','')}" for r in ranges)
    prompt = VERIFY_PROMPT.format(
        symbol=candidate.get("symbol", ""),
        meaning=candidate.get("meaning", ""),
        evidence=evidence,
    )
    log(f"verifying candidate {candidate.get('id')} against its own frames...")
    raw = client.chat(prompt, images=[sheet] if sheet else None, max_tokens=1000, temperature=0.2, tag="symbolic-verify")
    data = extract_json(raw)
    return data if isinstance(data, dict) else {"supported": None, "raw": raw[:400]}


def run(client, video: Path, workdir: Path, outline: str, segments: list, motifs: dict, swells: list, duration: float) -> list[dict]:
    cands = propose(client, outline, segments, motifs, swells, duration)
    for c in cands:
        c["verification"] = verify_candidate(client, video, c, workdir, duration)
        v = c["verification"]
        log(f"  candidate {c.get('id')}: supported={v.get('supported')}")
    return cands
