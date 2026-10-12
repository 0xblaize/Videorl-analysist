import json
import os
from pathlib import Path

HOME = Path.home()
AUTH_JSON = HOME / ".local" / "share" / "opencode" / "auth.json"
OPencode_CFG = HOME / ".config" / "opencode" / "opencode.json"

DEFAULT_BASE_URL = os.environ.get("VIDEORL_BASE_URL", "https://api.unorouter.com/v1")
DEFAULT_MODEL = os.environ.get("VIDEORL_MODEL", "gemini-3.1-flash-lite:free")
ANSWERER_MODEL = os.environ.get("VIDEORL_ANSWERER_MODEL", DEFAULT_MODEL)
RATE_LIMIT_SECONDS = float(os.environ.get("VIDEORL_RATE_LIMIT", "63"))
REQUEST_TIMEOUT = float(os.environ.get("VIDEORL_TIMEOUT", "180"))
MAX_RETRIES = int(os.environ.get("VIDEORL_MAX_RETRIES", "6"))
FRAME_MAX_W = int(os.environ.get("VIDEORL_FRAME_MAX_W", "768"))
JPEG_QUALITY = int(os.environ.get("VIDEORL_JPEG_Q", "80"))
WHISPER_MODEL = os.environ.get("VIDEORL_WHISPER", "base")


def _read_key() -> str:
    env_key = os.environ.get("VIDEORL_API_KEY")
    if env_key:
        return env_key
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
