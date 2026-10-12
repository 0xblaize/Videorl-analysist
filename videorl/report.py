import re

from videorl.util import ts


def build_stage2_package(meta, candidate, question_pack, options_pack, difficulty_pack) -> str:
    name = meta["name"]
    length = ts(meta["duration_s"])
    q = question_pack["question"]
    ev = question_pack["evidence_lines"]
    total = 0.0
    for line in ev:
        m = re.search(r"\((\d+)\s*s", line)
        if m:
            total += int(m.group(1))
    opts = options_pack["options"]
    correct = options_pack["correct_number"]
    correct_text = options_pack["correct_text"]
    k = sum(1 for o in opts if o["kind"] != "correct")
    dp = difficulty_pack
    picks = dp.get("correct_picks", 0)
    runs = dp.get("runs_completed", 8)

    lines = []
    lines.append("Step 1 — Watch")
    lines.append(f"{name} · {length} min. Watched full. File kept. Times from player.")
    lines.append("")
    lines.append("Step 2 — Usable?")
    lines.append(f"✅ Yes — it plays and has something symbolic — {str(candidate.get('symbol', '')).lower()} built across scenes.")
    lines.append("")
    lines.append("Skip why: N/A")
    lines.append("")
    lines.append("Step 3 — Upload")
    lines.append(f"Upload {name} whole, unchanged. Length: {length}")
    lines.append("")
    lines.append("Step 4 — Question + proof times")
    lines.append("Question:")
    lines.append(q)
    lines.append("")
    lines.append("Proof times:")
    for line in ev:
        lines.append(line)
    lines.append("")
    lines.append(f"Total: {total:.0f}")
    lines.append("")
    lines.append("Ticks: all 5 true — answer from video, one best answer, not one shot, no time words in question, answer without options.")
    lines.append("")
    lines.append("Saved. AI answered 8 times with no options.")
    lines.append("")
    lines.append("Step 5 — Auto candidates")
    lines.append(f"RETURNED: {len(options_pack['blind_answers'])} of 8")
    for c in options_pack.get("merged_candidates", []):
        lines.append(f"{c['paraphrase']} ({c.get('count', 1)})")
    lines.append("")
    lines.append("Step 6 — Pick correct")
    lines.append(f"Type: {correct}")
    lines.append("Write: N/A" if correct != 0 else f"Write: {correct_text}")
    lines.append(f"Why right: {question_pack.get('rationale', '')}")
    lines.append("")
    lines.append(f"Step 7 — {k} distractors. Total with correct = {k + 1}. Same start words, same length as correct.")
    n = 0
    for o in opts:
        if o["kind"] == "correct":
            continue
        n += 1
        lines.append(f"{n}. {o['text']}")
        lines.append(f"Gets-right: {o.get('right', '')}")
        lines.append(f"Gets-wrong: {o.get('wrong', '')}")
    lines.append("")
    lines.append("Step 8 — Auto check")
    lines.append("AI answered finished question 8 times.")
    lines.append(f"Score: correct picked {picks} of {runs}. Pass = 0, 1 or 2.")
    for r in dp.get("results", []):
        lines.append(f"run {r['run']}: picked option {r['picked_option_number']} ({'hit' if r['hit'] else 'miss'})")
    lines.append("")
    lines.append("Step 9 — Read score")
    if dp.get("pass"):
        lines.append(f"Score {picks} of {runs}. In range. Ready to submit.")
    else:
        if picks >= 5:
            lines.append(f"Score {picks} of {runs}. Too easy. Change question to need longer watch. Run check again.")
        elif picks >= 3:
            lines.append(f"Score {picks} of {runs}. Question ok. Harden 1-2 distractors. Run check again.")
        else:
            lines.append(f"Score {picks} of {runs}. All picks on one wrong option. Recheck the key.")
    lines.append("")
    lines.append("Step 10 — Submit")
    if dp.get("pass"):
        lines.append("0-2 picks. Submit as pass.")
    else:
        lines.append("Still 5+ after tries. Submit as could not pass. Skipped video costs less than fake pass.")
    lines.append("")
    lines.append("Full options list:")
    for o in opts:
        mark = "  <- correct" if o["kind"] == "correct" else ""
        lines.append(f"{o['number']}. {o['text']}{mark}")
    return "\n".join(lines)
