import random
import re

from videorl.options import evidence_frame_sheet
from videorl.util import log
from videorl.vlm import VLMClient

PROMPT = """You are answering a hard multiple-choice question about a film. The attached contact sheet shows frames from the evidence ranges (labels show which range). Choose the option that best matches what the film establishes symbolically.

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
    results = []
    correct_picks = 0
    for i in range(runs):
        order = list(range(1, len(options) + 1))
        rng.shuffle(order)
        lines = []
        true_pos = None
        for pos, num in enumerate(order, 1):
            o = options[num - 1]
            lines.append(f"{pos}. {o['text']}")
            if num == correct_number:
                true_pos = pos
        log(f"difficulty run {i + 1}/{runs}")
        try:
            raw = client.chat(
                PROMPT.format(question=question, options="\n".join(lines)),
                images=[sheet] if sheet else None,
                max_tokens=150,
                temperature=0.7,
                tag=f"difficulty-{i + 1}",
            )
        except Exception as e:
            log(f"  run failed: {e}")
            continue
        from videorl.util import extract_json

        data = extract_json(raw)
        try:
            pick = int((data or {}).get("pick", 0))
        except Exception:
            m = re.search(r"\d+", raw)
            pick = int(m.group()) if m else 0
        picked_num = order[pick - 1] if 1 <= pick <= len(order) else 0
        hit = picked_num == correct_number
        correct_picks += 1 if hit else 0
        results.append({"run": i + 1, "pick_position": pick, "picked_option_number": picked_num, "hit": hit})
        log(f"  picked option {picked_num} ({'HIT' if hit else 'miss'})")
    passed = correct_picks <= 2
    summary = {
        "runs_attempted": runs,
        "runs_completed": len(results),
        "correct_picks": correct_picks,
        "pass": passed,
        "threshold": "correct picked <= 2 of 8",
        "results": results,
    }
    log(f"difficulty: correct picked {correct_picks}/{len(results)} -> {'PASS' if passed else 'FAIL'}")
    return summary
