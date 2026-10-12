import json
import os
from pathlib import Path

HOME = Path.home()
AUTH_JSON = HOME / ".local" / "share" / "opencode" / "auth.json"
OPencode_CFG = HOME / ".config" / "opencode" / "opencode.json"
KEY_FILE = HOME / ".videorl_key"

GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GOOGLE_MODEL = "gemini-3.5-flash"  # 3.8-flash free tier is only 20 req/day — cannot finish one run

DEFAULT_BASE_URL = os.environ.get("VIDEORL_BASE_URL", "")
DEFAULT_MODEL = os.environ.get("VIDEORL_MODEL", "")
ANSWERER_MODEL = os.environ.get("VIDEORL_ANSWERER_MODEL", "")
_RATE_ENV = os.environ.get("VIDEORL_RATE_LIMIT", "")
REQUEST_TIMEOUT = float(os.environ.get("VIDEORL_TIMEOUT", "240"))
MAX_RETRIES = int(os.environ.get("VIDEORL_MAX_RETRIES", "6"))
FRAME_MAX_W = int(os.environ.get("VIDEORL_FRAME_MAX_W", "768"))
JPEG_QUALITY = int(os.environ.get("VIDEORL_JPEG_Q", "80"))
WHISPER_MODEL = os.environ.get("VIDEORL_WHISPER", "tiny")


def _read_key() -> str:
    for getter in (
        lambda: os.environ.get("VIDEORL_API_KEY"),
        lambda: os.environ.get("GEMINI_API_KEY"),
        lambda: KEY_FILE.read_text().strip() if KEY_FILE.exists() else "",
    ):
        k = getter()
        if k:
            return k.strip()
    if AUTH_JSON.exists():
        try:
            auth = json.loads(AUTH_JSON.read_text())
        except Exception:
            auth = {}
        for provider in ("unorouter", "unorouter-live", "bai", "agentrouter"):
            entry = auth.get(provider)
            if isinstance(entry, dict) and entry.get("key"):
                return entry["key"]
    if OPencode_CFG.exists():
        try:
            cfg = json.loads(OPencode_CFG.read_text())
            for prov in cfg.get("provider", {}).values():
                key = prov.get("options", {}).get("apiKey")
                if key:
                    return key
        except Exception:
            pass
    return ""


API_KEY = _read_key()
_USING_GOOGLE = (GOOGLE_BASE in DEFAULT_BASE_URL) or (
    not DEFAULT_BASE_URL and (KEY_FILE.exists() or bool(os.environ.get("GEMINI_API_KEY")))
)


def _pick_base() -> str:
    if DEFAULT_BASE_URL:
        return DEFAULT_BASE_URL
    if _USING_GOOGLE:
        return GOOGLE_BASE
    return "https://api.unorouter.com/v1"


def _pick_model() -> str:
    if DEFAULT_MODEL:
        return DEFAULT_MODEL
    if _USING_GOOGLE:
        return GOOGLE_MODEL
    return "gemini-3.1-flash-lite:free"


def _pick_rate() -> float:
    if _RATE_ENV:
        return float(_RATE_ENV)
    return 1.0 if _USING_GOOGLE else 63.0


DEFAULT_BASE_URL = _pick_base()
DEFAULT_MODEL = _pick_model()
ANSWERER_MODEL = ANSWERER_MODEL or DEFAULT_MODEL
RATE_LIMIT_SECONDS = _pick_rate()


def _unorouter_key() -> str:
    if AUTH_JSON.exists():
        try:
            auth = json.loads(AUTH_JSON.read_text())
        except Exception:
            return ""
        for name in ("unorouter", "unorouter-live"):
            e = auth.get(name)
            if isinstance(e, dict) and e.get("key"):
                return e["key"]
    return ""


PROVIDERS_FILE = HOME / ".videorl_providers.json"

OPENROUTER_MODEL = os.environ.get("VIDEORL_OPENROUTER_MODEL", "openai/gpt-4o")
GROQ_MODEL = os.environ.get("VIDEORL_GROQ_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct")


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
    if DEFAULT_BASE_URL and DEFAULT_MODEL and not _USING_GOOGLE:
        out.append({"base": DEFAULT_BASE_URL, "model": DEFAULT_MODEL, "key": API_KEY, "gap": 2.0})
    prov = _extra_providers()
    if prov.get("openrouter"):
        out.append({"base": "https://openrouter.ai/api/v1", "model": OPENROUTER_MODEL,
                    "key": prov["openrouter"], "gap": 2.0})
    if prov.get("groq"):
        out.append({"base": "https://api.groq.com/openai/v1", "model": GROQ_MODEL,
                    "key": prov["groq"], "gap": 2.0})
    return out
