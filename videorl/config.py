import json
import os
from pathlib import Path

HOME = Path.home()
KEY_FILE = HOME / ".videorl_key"
PROVIDERS_FILE = HOME / ".videorl_providers.json"

DEFAULT_BASE_URL = os.environ.get("VIDEORL_BASE_URL", "")
DEFAULT_MODEL = os.environ.get("VIDEORL_MODEL", "")
RATE_LIMIT_SECONDS = float(os.environ.get("VIDEORL_RATE_LIMIT", "1"))
REQUEST_TIMEOUT = float(os.environ.get("VIDEORL_TIMEOUT", "240"))
MAX_RETRIES = int(os.environ.get("VIDEORL_MAX_RETRIES", "6"))
FRAME_MAX_W = int(os.environ.get("VIDEORL_FRAME_MAX_W", "768"))
JPEG_QUALITY = int(os.environ.get("VIDEORL_JPEG_Q", "80"))
WHISPER_MODEL = os.environ.get("VIDEORL_WHISPER", "tiny")

DEFAULT_OPENROUTER_MODELS = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "thinkingmachines/inkling:free",
    "dots-studio/dots-3-note-preview:free",
    "openai/gpt-4o-mini",
]
OPENROUTER_MODELS = [
    m.strip() for m in os.environ.get("VIDEORL_OPENROUTER_MODEL", "").split(",") if m.strip()
] or DEFAULT_OPENROUTER_MODELS
GROQ_MODEL = os.environ.get("VIDEORL_GROQ_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct")


def _read_key() -> str:
    k = os.environ.get("VIDEORL_API_KEY", "")
    if k:
        return k.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    return ""


API_KEY = _read_key()


def _extra_providers() -> dict:
    if PROVIDERS_FILE.exists():
        try:
            data = json.loads(PROVIDERS_FILE.read_text())
            return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
        except Exception:
            return {}
    return {}


def routes() -> list[dict]:
    out = []
    if DEFAULT_BASE_URL and DEFAULT_MODEL:
        out.append({"base": DEFAULT_BASE_URL, "model": DEFAULT_MODEL, "key": API_KEY, "gap": 2.0})
    prov = _extra_providers()
    if prov.get("openrouter"):
        for m in OPENROUTER_MODELS:
            out.append({"base": "https://openrouter.ai/api/v1", "model": m,
                        "key": prov["openrouter"], "gap": 2.0})
    if prov.get("groq"):
        out.append({"base": "https://api.groq.com/openai/v1", "model": GROQ_MODEL,
                    "key": prov["groq"], "gap": 2.0})
    return out
