import re

TIME_CODE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
SECOND_COUNT = re.compile(r"\b\d+\s*(seconds?|secs?|s\b)\b", re.I)
POSITION_WORDS = [
    "near the end", "at the end", "near the start", "at the start", "in the start",
    "the beginning of the video", "at the beginning", "near the beginning",
    "in the first half", "in the first part", "in the first third", "in the opening",
    "the first scene", "the opening scene", "the opening shot", "in the last scene",
    "the last scene", "the final scene", "the final shot", "near the finish",
    "halfway through", "at the halfway", "the first race of the video",
    "the first half", "the second half", "the first clip", "the last clip",
]
BAD_QUESTION = ["which of the following", "which one of the following", "from the options", "choose from"]
FILLER = [
    "function as", "serves to", "acts as", "act as", "delve", "delves", "explore the",
    "journey", "underscores", "nuanced", "recurring presence", "cinematic device",
    "thematically", "narrative arc", "serves as a", "acts as a", "functions as",
]
BANNED_ING = ["is showing", "are showing", "is representing", "are representing", "is serving", "is acting", "is functioning", "is highlighting", "is underscoring"]


def check_style(text: str) -> list[str]:
    problems = []
    t = text.lower()
    for f in FILLER:
        if f in t:
            problems.append(f"filler phrase: '{f}'")
    for b in BANNED_ING:
        if b in t:
            problems.append(f"progressive verb: '{b}'")
    return problems


def check_question(q: str) -> list[str]:
    problems = []
    q_l = q.lower().strip()
    if not q or len(q) < 25:
        problems.append("question too short / empty")
    if TIME_CODE.search(q):
        problems.append("contains a time code (m:ss)")
    for p in POSITION_WORDS:
        if p in q_l:
            problems.append(f"contains position word/phrase: '{p}'")
    for b in BAD_QUESTION:
        if b in q_l:
            problems.append(f"not self-contained: '{b}'")
    problems += check_style(q)
    if q_l.endswith("?") is False and "?" not in q:
        problems.append("question should be an actual question")
    return problems


_LEAK_STOP = {
    "about", "which", "their", "there", "after", "before", "during", "while", "where",
    "what", "when", "does", "into", "over", "more", "than", "then", "them", "they",
    "from", "with", "have", "this", "that", "these", "those", "been", "were", "says",
    "said", "show", "shown", "film", "scene", "movie", "video", "character", "characters",
    "people", "represents", "represent", "broadcast", "broadcasts", "radio", "voice",
    "voices", "sounds", "sound", "image", "images", "visual", "moment", "moments",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", (text or "").lower())


def check_meaning_leak(question: str, meaning: str) -> list[str]:
    qw = set(_words(question))
    hits = sorted({w for w in _words(meaning) if len(w) >= 5 and w not in _LEAK_STOP and w in qw})
    if len(hits) >= 2:
        return [f"question gives away the answer (uses the meaning's own words: {', '.join(hits[:4])})"]
    return []


RANGE_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(:\d{2})?)\s*-\s*(\d{1,2}:\d{2}(:\d{2})?)\s*\((\d+)\s*s")


def check_evidence_lines(lines: list[str], duration: float) -> list[str]:
    problems = []
    if not lines:
        problems.append("no evidence lines")
    total = 0.0
    for line in lines:
        m = RANGE_LINE.match(line)
        if not m:
            problems.append(f"evidence line not in 'm:ss-m:ss (NN s, ...)' format: {line[:60]}")
            continue
        start = _parse(m.group(1))
        end = _parse(m.group(3))
        claimed = int(m.group(5))
        actual = end - start
        if end <= start:
            problems.append(f"range end<=start: {line[:60]}")
        if abs(actual - claimed) > 2:
            problems.append(f"claimed {claimed}s but range is {actual:.0f}s: {line[:60]}")
        if start < -1 or end > duration + 2:
            problems.append(f"range outside video duration: {line[:60]}")
        total += actual
    if total <= 30:
        problems.append(f"total evidence {total:.0f}s <= 30s (need >30s)")
    return problems


def _parse(t: str) -> float:
    parts = [int(p) for p in t.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def check_options(options: list[str], correct_index: int) -> list[str]:
    problems = []
    if len(options) < 8:
        problems.append(f"only {len(options)} options; need >=8 (1 correct + >=7 distractors)")
    if correct_index < 0 or correct_index >= len(options):
        problems.append("correct_index out of range")
    for i, o in enumerate(options):
        if not o or len(o) < 10:
            problems.append(f"option {i + 1} too short")
        problems += [f"option {i + 1}: {p}" for p in check_style(o)]
    frames = [o.split(",")[0][:40].lower() for o in options]
    if len(set(frames)) < len(frames) * 0.6:
        problems.append("options do not share a consistent grammatical frame")
    return problems


def check_all(question: str, evidence_lines: list[str], options: list[str], correct_index: int, duration: float) -> list[str]:
    return (
        [f"question: {p}" for p in check_question(question)]
        + [f"evidence: {p}" for p in check_evidence_lines(evidence_lines, duration)]
        + [f"options: {p}" for p in check_options(options, correct_index)]
    )
