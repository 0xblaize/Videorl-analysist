import base64
import json
import re
import threading
import time
from pathlib import Path

import httpx

from videorl import config
from videorl.util import log

STATE_DIR = Path.home() / ".cache" / "videorl"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LAST_CALL_FILE = STATE_DIR / "last_call_ts"
CALL_LOG = STATE_DIR / "calls.jsonl"

_lock = threading.Lock()
_gap = max(1.0, config.RATE_LIMIT_SECONDS)
_floor = _gap
_next_at = 0.0
_ok_streak = 0
_last_throttle = 0.0


def _respect_rate_limit() -> None:
    global _next_at
    with _lock:
        now = time.time()
        wait = _next_at - now
        if wait > 0:
            time.sleep(wait)
        _next_at = time.time() + _gap


def _on_success() -> None:
    global _gap, _ok_streak
    with _lock:
        _ok_streak += 1
        if _ok_streak >= 12 and _gap > _floor:
            _gap = max(_floor, _gap * 0.75)
            _ok_streak = 0


def _on_throttle(hint_seconds: int | None = None) -> None:
    global _gap, _ok_streak, _last_throttle
    with _lock:
        if hint_seconds is not None and hint_seconds <= 90:
            _gap = max(_gap, min(70.0, float(hint_seconds) + 8.0))
            _ok_streak = 0
            return
        now = time.time()
        if now - _last_throttle < 60:
            return
        _last_throttle = now
        target = float(hint_seconds) if hint_seconds else 5.0
        _gap = min(120.0, max(_gap * 1.5, 5.0, target))
        _ok_streak = 0
        log(f"server throttled us — slowing to {_gap:.0f}s between calls (auto-recovers)")


def _mark_call() -> None:
    LAST_CALL_FILE.write_text(str(time.time()))


def _log_call(tag: str, model: str, status: int, seconds: float, note: str = "") -> None:
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag,
        "model": model,
        "status": status,
        "seconds": round(seconds, 2),
        "note": note[:300],
    }
    with open(CALL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def video_part(path: Path) -> dict:
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{b64}"}}


class VLMClient:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        self.routes = list(config.routes())
        if base_url and api_key:
            self.routes.insert(0, {"base": base_url.rstrip("/"), "model": model or config.DEFAULT_MODEL,
                                   "key": api_key, "gap": 1.0, "vision": True})
        self.dead: set[int] = set()

    @property
    def model(self) -> str:
        alive = [i for i in range(len(self.routes)) if i not in self.dead]
        return self.routes[alive[0] if alive else 0]["model"]

    def available(self) -> bool:
        return bool(self.routes) and any(r["key"] for r in self.routes)

    def _pool(self, need_vision: bool) -> list[int]:
        pool = [i for i in range(len(self.routes)) if i not in self.dead]
        if need_vision:
            pool = [i for i in pool if self.routes[i].get("vision", True)]
        else:
            pool.sort(key=lambda i: 0 if self.routes[i].get("main") else 1)
        return pool

    def chat(
        self,
        prompt: str,
        images: list[Path] | None = None,
        video: Path | None = None,
        json_mode: bool = False,
        max_tokens: int = 2000,
        temperature: float = 0.4,
        tag: str = "chat",
        model: str | None = None,
    ) -> str:
        global _gap, _floor
        need_vision = bool(images) or video is not None
        pool = self._pool(need_vision)
        if not pool:
            raise RuntimeError(
                "no model service is available right now (need one that sees images). "
                "Re-run in a minute — progress is kept."
            )
        last_err = ""
        quota_fails = 0
        token_clamped = False
        for attempt in range(config.MAX_RETRIES):
            if not pool:
                break
            route = self.routes[pool[0]]
            mdl = model or route["model"]
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images or []:
                content.append(image_part(Path(img)))
            if video is not None:
                content.append(video_part(Path(video)))
            body = {
                "model": mdl,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": content}],
            }
            if route.get("effort"):
                body["reasoning_effort"] = route["effort"]
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route['key']}",
            }
            url = f"{route['base'].rstrip('/')}/chat/completions"
            _respect_rate_limit()
            t0 = time.time()
            try:
                r = httpx.post(url, headers=headers, json=body, timeout=config.REQUEST_TIMEOUT)
            except Exception as e:
                _mark_call()
                _log_call(tag, mdl, 0, time.time() - t0, f"network: {e}")
                last_err = "connection failed"
                log(f"{tag}: connection problem, retrying quietly ({attempt + 1})")
                if len(pool) > 1:
                    pool.append(pool.pop(0))
                time.sleep(min(20, 5 * (attempt + 1)))
                continue
            dt = time.time() - t0
            _mark_call()

            def drop_route(reason: str) -> None:
                self.dead.add(pool[0])
                _log_call(tag, mdl, r.status_code, dt, reason)
                log(f"{mdl}: not usable right now — moving on")
                pool.pop(0)

            def rotate_route(reason: str) -> None:
                _log_call(tag, mdl, r.status_code, dt, reason)
                log(f"{mdl}: busy — moving on")
                pool.append(pool.pop(0))

            if r.status_code == 200:
                j = None
                try:
                    j = r.json()
                except Exception:
                    j = None
                if isinstance(j, dict) and j.get("error"):
                    last_err = "upstream busy"
                    if len(pool) > 1:
                        rotate_route(f"error-in-200: {str(j.get('error'))[:300]}")
                        continue
                    time.sleep(min(30, 8 * (attempt + 1)))
                    continue
                if isinstance(j, dict) and j.get("choices"):
                    text = j["choices"][0].get("message", {}).get("content") or ""
                    if text.strip():
                        _log_call(tag, mdl, 200, dt)
                        _on_success()
                        return text
                    _log_call(tag, mdl, 200, dt, "empty answer (thinking ate the tokens?)")
                    last_err = "empty answer"
                    if len(pool) > 1:
                        pool.append(pool.pop(0))
                    time.sleep(3)
                    continue
                last_err = "empty answer"
                time.sleep(5)
                continue

            note = r.text[:1200]
            _log_call(tag, mdl, r.status_code, dt, note)
            last_err = "service refused the request"
            low = note.lower()

            if r.status_code == 402 and "max_tokens" in low:
                if not token_clamped and body["max_tokens"] > 700:
                    token_clamped = True
                    body["max_tokens"] = max(700, int(body["max_tokens"] * 0.6))
                    log(f"{tag}: budget is tight — retrying with fewer tokens")
                    continue

            permanent = (
                r.status_code == 402
                or r.status_code == 404
                or (r.status_code == 400 and "model" in low)
                or (r.status_code == 429 and ("exceeded your current quota" in low or "per day" in low))
            )
            if permanent:
                dry_all_day = bool(re.search(r"retry in \d+h", note, re.I)) or r.status_code in (402, 404) or "per day" in low
                quota_fails += 1
                if not dry_all_day and quota_fails <= 2:
                    mshort = re.search(r"retry in (\d+)m", note, re.I)
                    wait = min(75, int(mshort.group(1)) * 60 + 5) if mshort else 40
                    log(f"{tag}: busy minute window — pausing {wait}s, then retrying")
                    time.sleep(wait)
                    continue
                drop_route(note[:300])
                continue

            wait = None
            m = re.search(r"retry in (\d+)", note, re.I)
            if r.status_code == 429:
                wait = int(m.group(1)) + 2 if m else 10
                _on_throttle(int(m.group(1)) if m else None)
            elif r.status_code >= 500:
                wait = min(30, 8 * (attempt + 1))
                log(f"{tag}: server busy, retrying quietly ({attempt + 1})")
            if wait is None:
                drop_route(note[:300])
                continue
            time.sleep(wait)
        raise RuntimeError(
            f"the model service did not answer for [{tag}] after several tries "
            "(details are in the hidden log). Re-run the same command — progress is kept."
        )

    def chat_many(self, prompts: list[dict], workers: int = 3) -> list:
        from concurrent.futures import ThreadPoolExecutor

        results = [None] * len(prompts)

        def one(i: int, kw: dict) -> None:
            try:
                results[i] = self.chat(**kw)
            except Exception as e:
                results[i] = e

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(one, i, kw) for i, kw in enumerate(prompts)]
            for f in futs:
                f.result()
        return results
