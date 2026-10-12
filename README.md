# videorl — VideoRL v7 Symbolic Analyst

**By [blaize (@0xblaize)](https://github.com/0xblaize)**

One command watches a video, finds what it symbolizes, and writes a finished Step 1–10 question package into a single `.txt` file. Built for the Project Videorl v7 task, category: Symbolic / Metaphorical Interpretation.

---

## What it does

```
videorl run videoname.mp4
```

1. Watches the full video (scene cuts, keyframes, English transcript, audio swells)
2. Captures every scene, spots recurring objects/places/looks
3. Builds 3–5 symbolic readings, each checked against its own footage
4. Writes one hard question + proof times (no time codes, no position words)
5. Gets 8 blind answers, merges them, picks the correct option, writes 7+ near-miss distractors
6. Runs the 8-run difficulty check (needs correct picked ≤2 of 8). If the question is too easy, it automatically writes a harder one and re-checks (up to 3 rounds)
7. Saves everything into one file

**Output:**

- `~/Downloads/videoname.txt` — the full Step 1–10 package. **This is the only file ever written to Downloads.**
- Working files (frames, sheets, transcript, json) live in the hidden cache `~/.cache/videorl/work/` and never clutter your folders.

**Speed:** a 20-minute video takes about 5–10 minutes (whisper transcribes while scenes are being detected, question/difficulty answers run 3 at a time). Ctrl-C any time — re-running the same command resumes where it stopped, nothing is redone.

---

## Install — Windows

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/). On the installer, tick **"Add python.exe to PATH"**.
2. Open **PowerShell** and run:
   ```powershell
   cd C:\path\to\videorl-analyst
   py -m pip install --user -e .
   ```
3. Check it works:
   ```powershell
   videorl --help
   ```
4. (Optional, for on-screen text in slides) Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) and add it to PATH. Skip if unsure — the tool works without it.

## Install — macOS

1. Install **Python 3.11+** (comes with recent macOS, or use [python.org](https://www.python.org/downloads/) or `brew install python3`).
2. Open **Terminal** and run:
   ```bash
   cd ~/path/to/videorl-analyst
   python3 -m pip install --user -e .
   ```
3. Check it works:
   ```bash
   videorl --help
   ```
4. If the system blocks the command: `python3 -m pip install --user --break-system-packages -e .`
5. (Optional) `brew install tesseract` for OCR. Works fine without it.

*Note: iPhone/iPad (iOS) cannot run this. macOS computers can.*

## Install — Linux

1. Install Python 3.11+, pip, and git:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-pip git
   ```
2. Install the tool:
   ```bash
   cd ~/path/to/videorl-analyst
   python3 -m pip install --user -e .
   ```
   If pip refuses (externally managed environment):
   ```bash
   python3 -m pip install --user --break-system-packages -e .
   ```
3. Make sure `~/.local/bin` is on your PATH (add to `~/.bashrc` or `~/.zshrc` if needed):
   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```
4. Check it works:
   ```bash
   videorl --help
   ```
5. (Optional) `sudo apt install tesseract-ocr` for OCR.

**ffmpeg is included automatically** — no separate install on any system.

---

## API keys (one time)

The tool needs a vision model. Put your keys in one file:

```bash
cat > ~/.videorl_providers.json <<'EOF'
{"openrouter": "sk-or-...", "groq": "gsk_..."}
EOF
chmod 600 ~/.videorl_providers.json
```

Either key alone is enough. It uses OpenRouter's main vision model first, then Groq's, and jumps between them on its own when one is out of quota, has no credit, or rejects a model — you never see those errors, only one short line when it switches.

Change models any time via env: `VIDEORL_OPENROUTER_MODEL`, `VIDEORL_GROQ_MODEL`, or point at any OpenAI-compatible API with `VIDEORL_BASE_URL` + `VIDEORL_MODEL` + `VIDEORL_API_KEY`.

---

## Usage

Put the video in your Downloads folder, then:

```bash
cd ~/Downloads
videorl run videoname.mp4
```

That's the whole thing. When it finishes it prints:

```
Everything saved in /home/you/Downloads/videoname.txt
```

Paste that txt into the platform. A URL works too: `videorl run https://example.com/video.mp4`

Interrupted or failed halfway? Run the same command again — finished steps are detected and skipped.

**Other commands:**

```
videorl harder video.mp4        # new question ~5x harder than the last passing one, full re-check
videorl run video.mp4 --harder  # same thing as a flag
videorl analyze videoname.mp4   # stage 1 only (readings + report)
videorl question videoname.mp4  # stage 2 only (question + check)
videorl status                  # shows what is done for each video
```

**Useful settings:**

| Env var | Default | Meaning |
|---|---|---|
| VIDEORL_API_KEY | from ~/.videorl_key | API key |
| VIDEORL_MODEL | (from providers file) | vision model |
| VIDEORL_BASE_URL | — | any OpenAI-compatible API address |
| VIDEORL_RATE_LIMIT | 1 (adaptive) | base seconds between calls; only grows if the server says 429 |
| VIDEORL_WHISPER | tiny | transcript size (tiny/fast … small/base/better) |
| VIDEORL_TIMEOUT | 240 | seconds before a model call is retried |

---

## Rules the tool enforces (Step 4 gate)

- No time codes, no "near the end"/"at the start"/"first half" in the question
- Self-contained — no "which of the following"
- More than 30 seconds of proof time, more than one moment
- One clearly best answer; distractors are half-right, one small change each
- Question and options pass a style check (no filler, no "is showing"-style progressive verbs)
- Difficulty check must land on the correct option 2 times or fewer out of 8

Builder: **blaize**
