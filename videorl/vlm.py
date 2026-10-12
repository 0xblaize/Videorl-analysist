import base64
import json
import re
import time
from pathlib import Path

import httpx

from videorl import config
from videorl.util import log

STATE_DIR = Path.home() / ".cache" / "videorl"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LAST_CALL_FILE = STATE_DIR / "last_call_ts"
CALL_LOG = STATE_DIR / "calls.jsonl"


def _respect_rate_limit() -> None:
    last = 0.0
    if LAST_CALL_FILE.exists():
        try:
            last = float(LAST_CALL_FILE.read_text().strip())
        except Exception:
            last = 0.0
    wait = config.RATE_LIMIT_SECONDS - (time.time() - last)
    if wait > 0:
        log(f"rate limit: waiting {wait:.0f}s")
        time.sleep(wait)


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
        self.model = model or config.DEFAULT_MODEL
        self.base_url = (base_url or config.DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or config.API_KEY

    def available(self) -> bool:
        return bool(self.api_key)

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
        model = model or self.model
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images or []:
            content.append(image_part(Path(img)))
        if video is not None:
            content.append(video_part(Path(video)))
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/chat/completions"
        last_err = ""
        for attempt in range(config.MAX_RETRIES):
            _respect_rate_limit()
            t0 = time.time()
            try:
                r = httpx.post(url, headers=headers, json=body, timeout=config.REQUEST_TIMEOUT)
            except Exception as e:
                _mark_call()
                last_err = f"network: {e}"
                log(f"{tag}: network error, retry {attempt + 1}: {e}")
                time.sleep(min(30, 5 * (attempt + 1)))
                continue
            dt = time.time() - t0
            _mark_call()
            if r.status_code == 200:
                try:
                    j = r.json()
                    text = j["choices"][0]["message"]["content"] or ""
                except Exception:
                    text = r.text
                _log_call(tag, model, 200, dt)
                return text
            note = r.text[:400]
            _log_call(tag, model, r.status_code, dt, note)
            wait = None
            m = re.search(r"retry in (\d+)", note, re.I)
            if m:
                wait = int(m.group(1)) + 3
            elif r.status_code in (429, 503):
                wait = min(90, 20 * (attempt + 1))
            last_err = f"HTTP {r.status_code}: {note}"
            if wait is None:
                break
            log(f"{tag}: HTTP {r.status_code}, backing off {wait}s (attempt {attempt + 1})")
            time.sleep(wait)
        raise RuntimeError(f"VLM call failed [{tag}]: {last_err}")
