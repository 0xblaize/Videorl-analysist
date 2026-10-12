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
GROQ_MODEL = os.environ.get("VIDEORL_GROQ_MODEL", "openai/gpt-oss-120b")

OPENROUTER_FREE = [m for m in OPENROUTER_MODELS if ":free" in m]
OPENROUTER_PAID = [m for m in OPENROUTER_MODELS if ":free" not in m]

# Free vision providers (first 3): add the key to ~/.videorl_providers.json
# and it joins the front of the image-call chain automatically.
# cloudflare value format: "<account_id>:<api_token>" or
#   {"account": "...", "key": "...", "model": "..."}  (model optional)
VISION_PROVIDERS = [
    {"name": "nvidia", "base": "https://integrate.api.nvidia.com/v1",
     "model": os.environ.get("VIDEORL_NVIDIA_MODEL", "meta/llama-3.2-90b-vision-instruct")},
    {"name": "cloudflare", "base": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
     "model": os.environ.get("VIDEORL_CLOUDFLARE_MODEL", "@cf/meta/llama-3.2-11b-vision-instruct"),
     "split": True},
    {"name": "sambanova", "base": "https://api.sambanova.ai/v1",
     "model": os.environ.get("VIDEORL_SAMBANOVA_MODEL", "gemma-4-31B-it-preview")},
]


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
            out = {}
            for k, v in data.items():
                if isinstance(v, str) and v.strip():
                    out[k] = v.strip()
                elif isinstance(v, dict) and (v.get("key") or v.get("account")):
                    out[k] = v
            return out
        except Exception:
            return {}
    return {}


def routes() -> list[dict]:
    out = []
    prov = _extra_providers()
    if prov.get("groq"):
        out.append({"base": "https://api.groq.com/openai/v1", "model": GROQ_MODEL,
                    "key": prov["groq"], "gap": 2.0, "main": True, "vision": False,
                    "effort": "low"})
    if DEFAULT_BASE_URL and DEFAULT_MODEL:
        out.append({"base": DEFAULT_BASE_URL, "model": DEFAULT_MODEL, "key": API_KEY,
                    "gap": 2.0, "vision": True})
    for spec in VISION_PROVIDERS:
        v = prov.get(spec["name"])
        if not v:
            continue
        base, key, model = spec["base"], "", spec["model"]
        if isinstance(v, str):
            key = v
        else:
            key = v.get("key", "")
            model = v.get("model", model)
        if spec.get("split"):
            acct = v.get("account", "") if isinstance(v, dict) else ""
            if not acct:
                acct, sep, tok = key.partition(":")
                key = tok if sep else ""
            if not acct or not key:
                continue
            base = spec["base"].format(account=acct)
        if not key:
            continue
        out.append({"base": base, "model": model, "key": key,
                    "gap": 2.0, "vision": True, "name": spec["name"]})
    if prov.get("openrouter"):
        for m in OPENROUTER_FREE:
            out.append({"base": "https://openrouter.ai/api/v1", "model": m,
                        "key": prov["openrouter"], "gap": 2.0, "vision": True})
    if prov.get("openrouter"):
        for m in OPENROUTER_PAID:
            out.append({"base": "https://openrouter.ai/api/v1", "model": m,
                        "key": prov["openrouter"], "gap": 2.0, "vision": True})
    return out
