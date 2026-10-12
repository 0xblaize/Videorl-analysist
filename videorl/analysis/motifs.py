import re
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

from videorl.util import log, ts
from videorl.vlm import VLMClient

STOP = set(
    """a an the and or but if then than that this these those of to in on at by for with from as is are was were be been being he she it we they you i me my your his her their our us them do does did not no yes so what which who whom when where why how can could would should will shall may might must have has had about into over under out up down off again more most other some such only own same very just now also here there all any both each few nor own s t don now""".split()
)


def hist_signature(keyframe: str) -> np.ndarray | None:
    img = cv2.imread(keyframe)
    if img is None:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def visual_clusters(scenes: list[dict], threshold: float = 0.82) -> list[list[int]]:
    sigs = {}
    for s in scenes:
        kf = s.get("keyframe")
        if not kf:
            continue
        h = hist_signature(kf)
        if h is not None:
            sigs[s["idx"]] = h
    idxs = sorted(sigs)
    parent = {i: i for i in idxs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(idxs)):
        for j in range(i + 1, len(idxs)):
            a, b = sigs[idxs[i]], sigs[idxs[j]]
            if float(np.dot(a, b)) >= threshold:
                union(idxs[i], idxs[j])
    groups = defaultdict(list)
    for i in idxs:
        groups[find(i)].append(i)
    clusters = [sorted(v) for v in groups.values() if len(v) >= 3]
    clusters.sort(key=len, reverse=True)
    return clusters


def transcript_terms(segments: list[dict], top_n: int = 25) -> list[dict]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, float] = {}
    last_seen: dict[str, float] = {}
    for s in segments:
        for tok in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", s["text"].lower()):
            if tok in STOP:
                continue
            counter[tok] += 1
            first_seen.setdefault(tok, s["start"])
            last_seen[tok] = s["end"]
    out = []
    for term, n in counter.most_common(top_n):
        if n < 3:
            continue
        out.append({"term": term, "count": n, "first": first_seen[term], "last": last_seen[term]})
    return out


def caption_objects(captions: list[dict], scenes: list[dict], min_scenes: int = 3) -> list[dict]:
    scene_span = {s["idx"]: (s["start"], s["end"]) for s in scenes}
    seen_in: dict[str, set] = defaultdict(set)
    for c in captions:
        try:
            idx = int(c.get("idx"))
        except Exception:
            continue
        blob = f"{c.get('objects','')} {c.get('visual_notes','')} {c.get('setting','')}"
        for raw in re.split(r"[,;/]| and | with ", blob.lower()):
            term = re.sub(r"[^a-z0-9 -]", "", raw).strip()
            if len(term) < 3 or term in STOP:
                continue
            seen_in[term].add(idx)
    out = []
    for term, idxs in seen_in.items():
        if len(idxs) < min_scenes:
            continue
        spans = [scene_span[i] for i in idxs if i in scene_span]
        if not spans:
            continue
        times = [s[0] for s in spans]
        out.append(
            {
                "motif": term,
                "scene_count": len(idxs),
                "scenes": sorted(idxs)[:20],
                "first_seen": min(times),
                "last_seen": max(times),
                "spread_s": round(max(times) - min(times), 1),
            }
        )
    out.sort(key=lambda m: (m["scene_count"], m["spread_s"]), reverse=True)
    return out[:20]


VERIFY_PROMPT = """Below is a list of visual recurrences found in a film (objects, places, looks that appear across many scenes over a long stretch). Some are deliberate symbolic devices; most are ordinary set-dressing or costume continuity.

Recurrence list:
{items}

From the transcript highlights:
{transcript}

Pick the 3-5 recurrences that a filmmaker is most likely using deliberately as a symbolic or metaphorical device in this story. For each, output JSON:
{{"picks": [{{"motif": <string>, "why_deliberate": <string>, "likely_meaning_hint": <string one clause>}}]}}
Return strict JSON only."""


def verify_motifs(client: VLMClient, motifs: list[dict], transcript_excerpt: str) -> list[dict]:
    if not motifs:
        return []
    items = "\n".join(
        f"- '{m['motif']}' in {m['scene_count']} scenes, from {ts(m['first_seen'])} to {ts(m['last_seen'])} (spread {m['spread_s']}s)"
        for m in motifs[:14]
    )
    log("verifying motifs with VLM (which recurrences look deliberate?)")
    raw = client.chat(
        VERIFY_PROMPT.format(items=items, transcript=transcript_excerpt),
        max_tokens=1200,
        temperature=0.3,
        tag="motif-verify",
    )
    from videorl.util import extract_json

    data = extract_json(raw)
    picks = (data or {}).get("picks", []) if isinstance(data, dict) else []
    by_motif = {m["motif"]: m for m in motifs}
    enriched = []
    for p in picks if isinstance(picks, list) else []:
        m = dict(p)
        base = by_motif.get(str(p.get("motif", "")).lower()) or by_motif.get(str(p.get("motif", "")))
        if base:
            m.update(base)
        enriched.append(m)
    return enriched


def run(client: VLMClient, scenes: list[dict], captions: list[dict], segments: list[dict]) -> dict:
    clusters = visual_clusters(scenes)
    log(f"{len(clusters)} recurring visual clusters (local)")
    terms = transcript_terms(segments)
    objects = caption_objects(captions, scenes)
    from videorl.analysis.captions import transcript_highlights

    verified = verify_motifs(client, objects + [{"motif": f"visual-look-cluster-{i}", "scene_count": len(c), "scenes": c[:20], "first_seen": min(c), "last_seen": max(c), "spread_s": 0} for i, c in enumerate(clusters[:6])], transcript_highlights(segments))
    return {
        "visual_clusters": [{"size": len(c), "scenes": c[:25]} for c in clusters[:12]],
        "transcript_terms": terms,
        "object_motifs": objects,
        "verified_deliberate": verified,
    }
