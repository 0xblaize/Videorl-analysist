from videorl import rules
from videorl.util import extract_json, log, ts
from videorl.vlm import VLMClient

DRAFT_PROMPT = """Write ONE hard question and its evidence for this film. Follow the format rules exactly.

Category: Symbolic / Metaphorical Interpretation. The question asks what something in the film stands for. One correct answer only.

The verified reading to build on:
- What it is: {symbol}
- Correct answer: {meaning}
- Use this angle: {angle}
- Real evidence times: {ranges}
- Strongest wrong reading: {rival}

Question rules — all must hold:
- EXACTLY one defensible answer. If two careful viewers could each defend a different reading, rewrite the question. Do not ask about the broad symbol (the whole broadcast, the whole object) — ask what ONE specific detail stands for: a particular image, a particular line of dialogue, a particular sound in the scene.
- NO GIVEAWAYS. The question must never contain the meaning's own key words (if the answer is about propaganda or morale, those words cannot appear in the question) and must never hint at intent ("deceptive intent", "to sow panic", "to undermine"). A person reading the question cold must need the video to answer it.
- Start from concrete things on screen, like this GOOD example: "After Willie sees the rusty ship covered in laundry, what does his fall into the water during the gear rescue say about him and the crew?"
- BAD example (never write like this): "Throughout the film, how does the recurring presence of the Captain's desk function as a metaphor for the transformation of the protagonist?"
- Plain words only. No filler: deep, rich, nuanced, explore, delve, journey, underscores, function as, serves to, acts as.
- No progressive "-ing" verbs (is showing, represents while...). Allowed "-ing": contrasting, using, following — nothing else.
- No time codes, no second counts, no position words (near the end, at the start, first half, opening scene, final scene).
- Self-contained. No "which of the following".
- Needs more than 30 seconds of the film and more than one moment.

Evidence rules:
- Use only REAL times from the list above.
- Format each line EXACTLY like: 9:30-10:45 (75 s, what that stretch shows)
- The seconds in parentheses must equal the range length. 2 to 4 lines. Total over 30 s.

Rationale rule (Step 6 of our format):
- Two short plain sentences. First sentence: what each time range shows, like "9:30 shows the laundry on the rust, 21:50 shows his fall." Second sentence: why the evidence proves the answer.

Output STRICT JSON:
{{"question": "<question>", "evidence_lines": ["<line>", ...], "rationale": "<two short sentences>"}}
JSON only."""


def draft(client: VLMClient, candidate: dict, duration: float, feedback: str = "") -> dict:
    ranges = "; ".join(
        f"{ts(float(r['start']))}-{ts(float(r['end']))} ({float(r['end']) - float(r['start']):.0f}s): {r.get('what_shows', '')}"
        for r in candidate.get("evidence_ranges", [])
    )
    prompt = DRAFT_PROMPT.format(
        symbol=candidate.get("symbol"),
        meaning=candidate.get("meaning"),
        angle=candidate.get("question_angle"),
        ranges=ranges or "(use the times above)",
        rival=candidate.get("rival_reading"),
    )
    if feedback:
        prompt += f"\n\nIMPORTANT — make this one HARDER:{feedback}"
    for attempt in range(3):
        log(f"drafting question (attempt {attempt + 1})...")
        try:
            raw = client.chat(prompt, max_tokens=3000, temperature=0.4, tag="question-draft")
        except Exception as e:
            log(f"  attempt {attempt + 1} failed: {e}")
            continue
        data = extract_json(raw)
        if not isinstance(data, dict) or not data.get("question"):
            log(f"  attempt {attempt + 1}: reply was truncated or not JSON, retrying. Reply starts: {str(raw)[:120]}")
            continue
        q = str(data["question"]).strip()
        ev = [str(x).strip() for x in data.get("evidence_lines", [])]
        problems = rules.check_question(q) + rules.check_evidence_lines(ev, duration)
        problems += rules.check_meaning_leak(q, str(candidate.get("meaning", "")))
        if not problems:
            return {"question": q, "evidence_lines": ev, "rationale": str(data.get("rationale", "")), "candidate_id": candidate.get("id")}
        log(f"  draft rejected: {problems}")
    raise RuntimeError("could not draft a rule-compliant question in 3 attempts")
