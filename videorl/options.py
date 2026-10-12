import difflib
import random
import re
from pathlib import Path

from videorl.frames import frames_at, sheet_from_frames
from videorl.util import extract_json, log, ts
from videorl.vlm import VLMClient

STYLE = """Style rules for every answer text:
- Plain words. No filler: deep, rich, nuanced, explore, delve, journey, underscores, function as, serves to, acts as.
- No progressive "-ing" verbs except contrasting/using/following.
- One short clause. Say what it means, not what it might mean."""

BLIND_PROMPT = """Watch the evidence frames from a film (each labeled with its time range), then answer the question below. You get NO options. Use only what the film shows.

Question: {question}

{style}

Reply with just the one short clause that is your answer."""

MERGE_PROMPT = """These are {n} blind answers to one film question. Answers that say the same thing get merged into one.

Question: {question}

Answers:
{answers}

{style}

Group answers with the SAME meaning (wording differences are fine). Output STRICT JSON:
{{"candidates": [{{"paraphrase": "<one uniform plain statement of the group's meaning>", "count": <int>}}]}}
JSON only."""

SELECT_PROMPT = """Question: {question}

Merged blind answers:
{candidates}

The film's verified correct answer is:
"{truth}"

Pick the numbered candidate that matches the truth. If none matches, output {{"correct": 0}}.
{style}
JSON only: {{"correct": <number or 0>}}"""

DISTRACTOR_PROMPT = """Write near-miss distractors for a hard film question.

Question: {question}
Correct answer: "{correct}"
Strongest wrong reading: "{rival}"
Wrong blind answers to reuse as ideas: {wrong}

Write 7-9 distractors. Rules:
- Same start words as the correct answer. Same length, near enough.
- Plain words only. {style}
- Each is half-right half-wrong. Change ONE small thing only — person, motive, feeling, power, result. Never fix a direction, order, or count.
- The gets-right sentence MUST use words that appear IN the option itself.
- The gets-wrong sentence says what the film really happens instead.
- No two may change the same thing about the same subject.

Output STRICT JSON:
{{"distractors": [{{"text": "<same frame as correct>", "right": "<gets-right, uses words from the option>", "wrong": "<gets-wrong + what really happens>"}}]}}
JSON only."""


def _parse_range_line(line: str) -> tuple[float, float] | None:
    m = re.match(r"^\s*(\d{1,2}:\d{2}(:\d{2})?)\s*-\s*(\d{1,2}:\d{2}(:\d{2})?)", line)
    if not m:
        return None

    def p(t):
        parts = [int(x) for x in t.split(":")]
        return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]

    return p(m.group(1)), p(m.group(3))


def evidence_frame_sheet(video: Path, evidence_lines: list[str], workdir: Path, tag: str) -> str | None:
    times = []
    labels = []
    for line in evidence_lines:
        rng = _parse_range_line(line)
        if not rng:
            continue
        s, e = rng
        for k in range(3):
            times.append(s + (e - s) * (k + 1) / 4)
            labels.append(line.split("(")[0].strip())
    if not times:
        return None
    paths = frames_at(video, times, workdir / "blind_frames", tag=tag)
    return sheet_from_frames(paths, workdir / "blind_sheets" / f"{tag}.jpg", labels=labels)


def blind_answers(client: VLMClient, video: Path, question: str, evidence_lines: list[str], workdir: Path, n: int = 8) -> list[dict]:
    sheet = evidence_frame_sheet(video, evidence_lines, workdir, "blind")
    answers = []
    for i in range(n):
        log(f"blind answer {i + 1}/{n}")
        prompt = BLIND_PROMPT.format(question=question, style=STYLE)
        try:
            raw = client.chat(
                prompt,
                images=[sheet] if sheet else None,
                max_tokens=250,
                temperature=0.5 + 0.1 * (i % 3),
                tag=f"blind-{i + 1}",
            )
        except Exception as e:
            log(f"  blind run {i + 1} failed: {e}")
            continue
        text = " ".join(raw.strip().split())
        answers.append({"run": i + 1, "answer": text})
        log(f"  -> {text[:110]}")
    return answers


def merge_candidates(client: VLMClient, question: str, answers: list[dict]) -> list[dict]:
    if not answers:
        return []
    listing = "\n".join(f"{a['run']}. {a['answer']}" for a in answers)
    raw = client.chat(
        MERGE_PROMPT.format(n=len(answers), question=question, answers=listing, style=STYLE),
        max_tokens=1200,
        temperature=0.2,
        tag="merge",
    )
    data = extract_json(raw)
    cands = (data or {}).get("candidates", []) if isinstance(data, dict) else []
    out = []
    for c in cands if isinstance(cands, list) else []:
        if c.get("paraphrase"):
            out.append({"paraphrase": str(c["paraphrase"]).strip(), "count": int(c.get("count", 1))})
    if not out:
        seen: list[str] = []
        for a in answers:
            if not any(difflib.SequenceMatcher(None, a["answer"], s).ratio() > 0.75 for s in seen):
                seen.append(a["answer"])
                out.append({"paraphrase": a["answer"], "count": 1})
    return out


def select_correct(client: VLMClient, question: str, candidates: list[dict], truth: str) -> int:
    listing = "\n".join(f"{i + 1}. {c['paraphrase']} (said by {c.get('count', 1)} blind runs)" for i, c in enumerate(candidates))
    raw = client.chat(
        SELECT_PROMPT.format(question=question, candidates=listing or "(none)", truth=truth, style=STYLE),
        max_tokens=150,
        temperature=0.0,
        tag="select",
    )
    data = extract_json(raw)
    try:
        idx = int((data or {}).get("correct", 0))
    except Exception:
        idx = 0
    if idx < 0 or idx > len(candidates):
        idx = 0
    return idx


def _frame_of(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).rstrip(".")[:60]


def write_distractors(client: VLMClient, question: str, correct: str, rival: str, wrong: list[str], n: int = 8) -> list[dict]:
    listing = "\n".join(f"- {w}" for w in wrong[:8]) or "(none)"
    raw = client.chat(
        DISTRACTOR_PROMPT.format(
            question=question,
            correct=correct,
            rival=rival or "(none given)",
            wrong=listing,
            style=STYLE,
        ),
        max_tokens=2500,
        temperature=0.6,
        tag="distractors",
    )
    data = extract_json(raw)
    dis = (data or {}).get("distractors", []) if isinstance(data, dict) else []
    out = []
    for d in dis if isinstance(dis, list) else []:
        text = str(d.get("text", "")).strip()
        if text and text.lower() != correct.lower().strip():
            out.append({"text": text, "right": str(d.get("right", "")), "wrong": str(d.get("wrong", ""))})
    return out[: max(n, 7)]


def build_options(
    client: VLMClient,
    video: Path,
    question: str,
    evidence_lines: list[str],
    candidate: dict,
    workdir: Path,
    n_blind: int = 8,
) -> dict:
    answers = blind_answers(client, video, question, evidence_lines, workdir, n=n_blind)
    merged = merge_candidates(client, question, answers)
    truth = str(candidate.get("meaning", "")).strip()
    correct_idx = select_correct(client, question, merged, truth)
    if correct_idx == 0:
        correct_text = truth
        log("no blind answer matched the truth; correct option written from the verified reading")
    else:
        correct_text = merged[correct_idx - 1]["paraphrase"]
        log(f"correct option from blind group #{correct_idx}")
    wrong = [c["paraphrase"] for i, c in enumerate(merged, 1) if i != correct_idx]
    distractors = write_distractors(client, question, correct_text, str(candidate.get("rival_reading", "")), wrong)
    options = [{"text": correct_text, "kind": "correct"}] + [
        {"text": d["text"], "kind": "distractor", "right": d["right"], "wrong": d["wrong"]} for d in distractors
    ]
    random.Random(7).shuffle(options)
    for i, o in enumerate(options, 1):
        o["number"] = i
    correct_number = next(o["number"] for o in options if o["kind"] == "correct")
    return {
        "blind_answers": answers,
        "merged_candidates": merged,
        "options": options,
        "correct_number": correct_number,
        "correct_text": correct_text,
        "distractors": distractors,
    }
