import random
import re

from videorl.options import evidence_frame_sheet
from videorl.util import extract_json, log
from videorl.vlm import VLMClient

PROMPT = """You are answering a hard multiple-choice question about a film. The attached contact sheet shows frames from the proof times (labels show which range). Choose the option that best matches what the film establishes symbolically.

Question: {question}

Options:
{options}

Think briefly, then output STRICT JSON: {{"pick": <option number>}}. JSON only."""


def run_difficulty(
    client: VLMClient,
    video,
    question: str,
    options: list[dict],
    correct_number: int,
    evidence_lines: list[str],
    workdir,
    runs: int = 8,
) -> dict:
    sheet = evidence_frame_sheet(video, evidence_lines, workdir, "difficulty")
    rng = random.Random()
    setups = []
    for _ in range(runs):
        order = list(range(1, len(options) + 1))
        rng.shuffle(order)
        lines = []
        true_pos = None
        for pos, num in enumerate(order, 1):
            lines.append(f"{pos}. {options[num - 1]['text']}")
            if num == correct_number:
                true_pos = pos
        setups.append({"order": order, "true_pos": true_pos})
    log(f"asking {runs} difficulty runs...")
    calls = [
        dict(
            prompt=PROMPT.format(question=question, options="\n".join(
                f"{pos}. {options[num - 1]['text']}" for pos, num in enumerate(s["order"], 1)
            )),
            images=[sheet] if sheet else None,
            max_tokens=150,
            temperature=0.7,
            tag=f"difficulty-{i + 1}",
        )
        for i, s in enumerate(setups)
    ]
    results = client.chat_many(calls, workers=1)
    out = []
    correct_picks = 0
    for i, (raw, s) in enumerate(zip(results, setups), 1):
        if isinstance(raw, Exception) or not raw:
            log(f"  run {i} failed: {raw}")
            continue
        data = extract_json(str(raw))
        try:
            pick = int((data or {}).get("pick", 0))
        except Exception:
            m = re.search(r"\d+", str(raw))
            pick = int(m.group()) if m else 0
        picked_num = s["order"][pick - 1] if 1 <= pick <= len(s["order"]) else 0
        hit = picked_num == correct_number
        correct_picks += 1 if hit else 0
        out.append({"run": i, "pick_position": pick, "picked_option_number": picked_num, "hit": hit})
        log(f"  run {i}: picked option {picked_num} ({'HIT' if hit else 'miss'})")
    passed = correct_picks <= 2
    summary = {
        "runs_attempted": runs,
        "runs_completed": len(out),
        "correct_picks": correct_picks,
        "pass": passed,
        "threshold": "correct picked <= 2 of 8",
        "results": out,
    }
    log(f"difficulty: correct picked {correct_picks}/{len(out)} -> {'PASS' if passed else 'FAIL'}")
    return summary
