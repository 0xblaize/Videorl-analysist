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
                                   "key": api_key, "gap": 1.0})
        self.ri = 0

    @property
    def model(self) -> str:
        return self.routes[self.ri]["model"]

    @property
    def base_url(self) -> str:
        return self.routes[self.ri]["base"]

    @property
    def api_key(self) -> str:
        return self.routes[self.ri]["key"]

    def available(self) -> bool:
        return any(r["key"] for r in self.routes)

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
        last_err = ""
        quota_fails = 0
        for attempt in range(config.MAX_RETRIES):
            route = self.routes[self.ri]
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
                last_err = f"network: {e}"
                log(f"{tag}: connection problem, retrying quietly ({attempt + 1})")
                time.sleep(min(20, 5 * (attempt + 1)))
                continue
            dt = time.time() - t0
            _mark_call()
            if r.status_code == 200:
                try:
                    j = r.json()
                    text = j["choices"][0]["message"]["content"] or ""
                except Exception:
                    text = r.text
                _log_call(tag, mdl, 200, dt)
                _on_success()
                quota_fails = 0
                return text
            note = r.text[:1200]
            _log_call(tag, mdl, r.status_code, dt, note)
            last_err = f"HTTP {r.status_code}: {note[:300]}"
            low = note.lower()
            route_problem = (
                r.status_code == 402
                or (r.status_code == 404)
                or (r.status_code == 400 and "model" in low)
                or (r.status_code == 429 and ("exceeded your current quota" in low or "per day" in low))
            )
            if route_problem:
                dry_all_day = bool(re.search(r"retry in \d+h", note, re.I)) or r.status_code in (402, 404) or "per day" in low
                quota_fails += 1
                if not dry_all_day and quota_fails <= 2:
                    mshort = re.search(r"retry in (\d+)m", note, re.I)
                    wait = min(75, int(mshort.group(1)) * 60 + 5) if mshort else 40
                    log(f"{tag}: busy minute window — pausing {wait}s, then retrying")
                    time.sleep(wait)
                    continue
                if self.ri + 1 < len(self.routes):
                    self.ri += 1
                    nxt = self.routes[self.ri]
                    global _gap, _floor
                    _gap = _floor = max(1.0, nxt.get("gap", 1.0))
                    log(f"{mdl}: not usable right now — switching to {nxt['model']}")
                    continue
                raise RuntimeError(
                    "every API route is out of quota or misconfigured. "
                    "Add keys to ~/.videorl_providers.json (openrouter, groq) or ~/.videorl_key."
                )
            wait = None
            m = re.search(r"retry in (\d+)", note, re.I)
            if r.status_code == 429:
                wait = int(m.group(1)) + 2 if m else 10
                _on_throttle(int(m.group(1)) if m else None)
            elif r.status_code == 503:
                wait = min(30, 8 * (attempt + 1))
                log(f"{tag}: server busy, retrying quietly ({attempt + 1})")
            if wait is None:
                break
            time.sleep(wait)
        raise RuntimeError(f"gave up on [{tag}]: {last_err[:200]}")

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
