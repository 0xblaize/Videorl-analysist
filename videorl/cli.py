import argparse
import os
import subprocess
import sys
from pathlib import Path

from videorl import rules
from videorl.util import analysis_dir_for, die, log, read_json, response_path_for, write_json

REPO_URL = "https://github.com/0xblaize/Videorl-analysist.git"


def _find_repo() -> Path | None:
    env = os.environ.get("VIDEORL_REPO", "")
    cands = []
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.extend(here.parents)
    cands.append(Path.home() / "Projects" / "videorl-analyst")
    for p in cands:
        try:
            if (p / ".git").exists() and (p / "pyproject.toml").exists() and (p / "videorl").is_dir():
                return p
        except Exception:
            continue
    return None


def cmd_update(_args) -> int:
    log("checking for a newer videorl...")
    try:
        repo = _find_repo()
        if repo:
            r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                log(f"{repo} has local edits — commit or stash them, then run videorl --update again")
                return 1
            subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet",
                            "--no-deps", "-e", str(repo)],
                           capture_output=True, text=True, timeout=300)
            head = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h %s"],
                                  capture_output=True, text=True, timeout=30)
            where = head.stdout.strip() if head.returncode == 0 else "latest"
        else:
            log("installing the latest version from github...")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet",
                                "--no-deps", f"git+{REPO_URL}"],
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                log("update failed — no connection to github, or pip had a problem")
                log("your current version is untouched; try again later")
                return 1
            where = "latest from github"
        log(f"videorl is now on {where}")
        log("re-run your command to use the new version")
        return 0
    except Exception:
        log("update failed — check your connection and try again")
        log("your current version is untouched")
        return 1


def cmd_analyze(args) -> int:
    from videorl.stage1 import run_stage1

    res = run_stage1(args.video, workdir=Path.cwd())
    print()
    print(res["report"])
    print(f"\nArtifacts: {res['adir']}")
    cands = res["candidates"]
    if not cands:
        print("No symbolic candidates survived validation — inspect captions.json / motifs.json.")
        return 2
    print("\nNext: videorl question <video> --candidate <N>")
    return 0


def cmd_question(args) -> int:
    from videorl.difficulty import run_difficulty
    from videorl.options import build_options
    from videorl.question import draft
    from videorl.report import build_stage2_package
    from videorl.vlm import VLMClient

    meta = None
    video = None
    for cand_path in _find_analysis(args.video):
        adir = cand_path.parent
        meta = read_json(adir / "meta.json")
        if meta:
            video = Path(meta["video_path"])
            break
    if not meta:
        die("no stage-1 artifacts found; run `videorl analyze` first")
    adir = analysis_dir_for(video)
    candidates = read_json(adir / "symbolic_candidates.json") or []
    if not candidates:
        die("no symbolic readings in stage 1")
    if args.candidate is None:
        def score(c):
            v = c.get("verification") or {}
            return (1 if v.get("supported") else 0, float(c.get("confidence") or 0))
        candidate = sorted(candidates, key=score, reverse=True)[0]
        log(f"using verified reading: {candidate.get('symbol')} — {candidate.get('meaning')}")
    else:
        candidate = next((c for c in candidates if str(c.get("id")) == str(args.candidate)), None)
        if not candidate:
            die(f"reading {args.candidate} not found")

    client = VLMClient()
    feedback = ""
    if getattr(args, "harder", False):
        prev = read_json(adir / "question.json")
        prev_q = str(prev.get("question", "")) if prev else ""
        if prev:
            write_json(adir / "question_v1.json", prev)
        feedback = (
            (f" The previous passing question was: '{prev_q}'. Do not repeat its phrasing or any of its key words." if prev_q else "")
            + " Write a NEW question on the same verified reading that is about 5 times harder than a standard pass: "
            "the answer must require connecting three or more separated moments, hinge on a subtler visual or audio detail, "
            "and stay far from the most direct symbol reading. Still exactly one defensible answer, no giveaways."
        )
        log("harder mode: drafting a 5x harder question than the last one")
    rounds = 0
    while True:
        rounds += 1
        question_pack = read_json(adir / "question.json")
        if question_pack is None or question_pack.get("candidate_id") != candidate.get("id") or feedback:
            question_pack = draft(client, candidate, meta["duration_s"], feedback=feedback)
            write_json(adir / "question.json", question_pack)
        log(f"question: {question_pack['question']}")

        options_pack = None if feedback else read_json(adir / "options.json")
        if options_pack is None:
            options_pack = build_options(
                client, video, question_pack["question"], question_pack["evidence_lines"], candidate, adir, n_blind=args.runs
            )
            write_json(adir / "options.json", options_pack)
        problems = rules.check_all(
            question_pack["question"],
            question_pack["evidence_lines"],
            [o["text"] for o in options_pack["options"]],
            options_pack["correct_number"] - 1,
            meta["duration_s"],
        )
        if problems:
            log(f"rule check warnings: {problems}")

        difficulty_pack = read_json(adir / "difficulty.json")
        if difficulty_pack is None or args.rerun_difficulty or feedback:
            difficulty_pack = run_difficulty(
                client,
                video,
                question_pack["question"],
                options_pack["options"],
                options_pack["correct_number"],
                question_pack["evidence_lines"],
                adir,
                runs=args.difficulty_runs,
            )
            write_json(adir / "difficulty.json", difficulty_pack)
        if difficulty_pack.get("pass"):
            break
        if rounds >= 3 or difficulty_pack.get("runs_completed", 0) < 6:
            break
        feedback = (
            f" The question '{question_pack['question']}' was picked correctly by "
            f"{difficulty_pack.get('correct_picks')} of {difficulty_pack.get('runs_completed')} test takers "
            "(we need 2 or fewer). Same reading, new harder question: make the answer require connecting "
            "separated moments, and avoid the most direct symbol reading."
        )
        log(f"question too easy ({difficulty_pack.get('correct_picks')}/{difficulty_pack.get('runs_completed')} correct) — drafting a harder one...")

    package = build_stage2_package(meta, candidate, question_pack, options_pack, difficulty_pack)
    out_file = response_path_for(video)
    out_file.write_text(package, encoding="utf-8")
    print()
    print(f"Everything saved in {out_file}")
    return 0 if difficulty_pack.get("pass") else 3


def cmd_run(args) -> int:
    from videorl.stage1 import run_stage1

    run_stage1(args.video)
    args.candidate = None
    args.runs = 8
    args.difficulty_runs = 8
    args.rerun_difficulty = False
    return cmd_question(args)


def _find_analysis(video_arg: str):
    p = Path(video_arg).expanduser()
    if p.exists():
        adir = analysis_dir_for(p.resolve())
        if (adir / "meta.json").exists():
            yield adir / "meta.json"
    cache_root = Path.home() / ".cache" / "videorl" / "work"
    if cache_root.exists():
        for d in sorted(cache_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if (d / "meta.json").exists():
                yield d / "meta.json"


def cmd_status(args) -> int:
    found = False
    for meta_path in _find_analysis(args.video or "."):
        adir = meta_path.parent
        meta = read_json(meta_path) or {}
        found = True
        print(f"== {adir.name}")
        for f in [
            "meta.json", "scenes.json", "keyframes", "contact_sheets", "audio.wav", "transcript.srt",
            "captions.json", "motifs.json", "symbolic_candidates.json", "question.json", "options.json",
            "difficulty.json", "stage1_report.md", "stage2_package.md",
        ]:
            p = adir / f
            print(f"  {'✓' if p.exists() else '·'} {f}")
        if meta:
            print(f"  duration={meta.get('duration_s')}s usable={meta.get('usable')}")
    if not found:
        print("No analysis folders found next to cwd or the given path.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="videorl", description="VideoRL v7 symbolic video question pipeline")
    ap.add_argument("--update", action="store_true",
                    help="pull the latest version from github")
    sub = ap.add_subparsers(dest="cmd", required=False)

    a = sub.add_parser("analyze", help="stage 1: deep analysis + symbolic readings")
    a.add_argument("video", help="mp4 file or URL (run from the folder holding the file)")
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("run", help="one command: full analysis + question + check, one txt file out")
    r.add_argument("video", help="mp4 file or URL")
    r.add_argument("--harder", action="store_true", help="draft a 5x harder question than the last passing one")
    r.set_defaults(func=cmd_run)

    hh = sub.add_parser("harder", help="like run, but forces a new question ~5x harder than the last passing one")
    hh.add_argument("video", help="mp4 file or URL")
    hh.set_defaults(func=cmd_run, harder=True)

    q = sub.add_parser("question", help="stage 2: question package + difficulty check")
    q.add_argument("video", help="same video file as analyze")
    q.add_argument("--candidate", type=str, default=None, help="candidate id from stage1_report.md")
    q.add_argument("--runs", type=int, default=8, help="blind answer runs (default 8)")
    q.add_argument("--difficulty-runs", type=int, default=8, help="difficulty check runs (default 8)")
    q.add_argument("--rerun-difficulty", action="store_true")
    q.set_defaults(func=cmd_question)

    s = sub.add_parser("status", help="show artifacts for analysis folders")
    s.add_argument("video", nargs="?", default=".")
    s.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    if getattr(args, "update", False):
        return cmd_update(args)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted — artifacts so far are kept; re-run the same command to resume")
        return 130
    except RuntimeError as e:
        print(f"\nstopped: {e}")
        print("re-run the same command when ready — finished steps are kept")
        return 1
    except Exception as e:
        print(f"\nunexpected problem: {e}")
        print("re-run the same command — finished steps are kept")
        return 1


if __name__ == "__main__":
    sys.exit(main())
