#!/usr/bin/env python3
"""job-apply-assistant — a personal, assisted job-application funnel.

    python apply.py sync            # pull the repo(s) in config.yaml into the tracker
    python apply.py sync <url>      # pull one specific repo README
    python apply.py list [--status new] [--search stripe] [--limit 20]
    python apply.py next            # next unapplied job: draft it + open the link
    python apply.py draft <key>     # (re)generate a tailored draft for one job
    python apply.py open <key>      # open a job's application link in your browser
    python apply.py mark <key> <status> [--note "..."]
    python apply.py stats           # funnel counts

Design principle: this tool does the tedious 90% (finding, dedupe, tracking,
drafting, opening pre-filled) and hands YOU the 10% that must stay human — the
final read and click of Submit. It never auto-submits. That keeps you inside
every portal's ToS and keeps a human behind every application.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import yaml

from assistant.draft import make_draft
from assistant.models import STATUSES, CATEGORY_ORDER
from assistant.parse import fetch_readme, parse_readme
from assistant.store import Store


def _by_priority(job):
    """Sort key: SWE first, then data, then PM, then other (see CATEGORY_ORDER)."""
    return CATEGORY_ORDER.index(job.category) if job.category in CATEGORY_ORDER \
        else len(CATEGORY_ORDER)

HERE = Path(__file__).resolve().parent


def load_yaml(path: Path, default: dict) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or default
    return default


def load_env(path: Path) -> None:
    """Read a simple KEY=value .env into os.environ (without overriding real env).

    Lets you keep GEMINI_API_KEY / ANTHROPIC_API_KEY in a git-ignored file instead
    of re-exporting them every shell. Values already set in the environment win.
    """
    if not path.exists():
        return
    import os
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_config() -> dict:
    load_env(HERE / ".env")
    return load_yaml(HERE / "config.yaml", {
        "db": "funnel.db", "drafts_dir": "drafts", "repos": [],
        "include_closed": False, "draft_provider": "auto", "hide_grad": True,
    })


def get_profile() -> dict:
    return load_yaml(HERE / "profile.yaml", {"name": "", "tagline": ""})


def _fmt(job) -> str:
    tag = {"applied": "✅", "interview": "📞", "offer": "🎉", "rejected": "❌",
           "skipped": "⏭️", "drafted": "📝", "new": "🆕"}.get(job.status, "•")
    cat = f"{job.category:<7}"  # swe/data-ml/pm/other, padded so columns line up
    loc = f"  · {job.location}" if job.location else ""
    return f"{tag} {cat} [{job.key}] {job.company} — {job.title}{loc}"


def cmd_sync(args, cfg, store):
    repos = [args.url] if args.url else cfg.get("repos", [])
    if not repos:
        print("No repos configured. Add one to config.yaml under `repos:` or run:\n"
              "  python apply.py sync <github-repo-url>")
        return 1
    total_added = total_seen = 0
    for url in repos:
        print(f"==> Fetching {url}")
        try:
            text = fetch_readme(url)
        except Exception as e:
            print(f"  ! failed: {e}")
            continue
        jobs = parse_readme(text, source=url, include_closed=cfg.get("include_closed", False))
        added, seen = store.upsert_many(jobs)
        print(f"    parsed {len(jobs)} jobs — {added} new, {seen} already tracked")
        total_added += added
        total_seen += seen
    print(f"==> Done. {total_added} new roles added to the funnel.")
    return 0


def _hide_grad(args, cfg) -> bool:
    """Advanced-degree roles are hidden unless --include-grad or config says show."""
    return not args.include_grad and cfg.get("hide_grad", True)


def cmd_list(args, cfg, store):
    # Fetch, then filter by category and order by priority in Python (the dataset
    # is small, and category is derived from the title rather than stored).
    jobs = store.list(status=args.status, search=args.search or "")
    if args.category:
        jobs = [j for j in jobs if j.category == args.category]
    hidden = 0
    if _hide_grad(args, cfg):
        before = len(jobs)
        jobs = [j for j in jobs if not j.is_grad]
        hidden = before - len(jobs)
    jobs.sort(key=_by_priority)  # stable: keeps first_seen order within a category
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        print("No matching jobs. Try `python apply.py sync` first.")
        return 0
    for j in jobs:
        print(_fmt(j))
    note = f"  ({hidden} advanced-degree hidden — see --include-grad)" if hidden else ""
    print(f"\n{len(jobs)} shown.{note}")
    return 0


def cmd_next(args, cfg, store):
    jobs = store.list(status="new")
    # Work the funnel in priority order: SWE, then data, then PM, then other.
    jobs.sort(key=_by_priority)
    if args.category:
        jobs = [j for j in jobs if j.category == args.category]
    if _hide_grad(args, cfg):
        jobs = [j for j in jobs if not j.is_grad]
    if not jobs:
        print("Nothing left marked 'new'. 🎉  Run `sync` or check `list --status drafted`.")
        return 0
    job = jobs[0]
    print(_fmt(job))
    provider = args.provider or cfg.get("draft_provider", "auto")
    path = make_draft(job, get_profile(), str(HERE / cfg.get("drafts_dir", "drafts")), provider)
    store.set_draft(job.key, path)
    print(f"    draft → {path}")
    if not args.no_open:
        print(f"    opening {job.url}")
        webbrowser.open(job.url)
    print(f"\nWhen you've applied:  python apply.py mark {job.key} applied")
    print(f"Not a fit:            python apply.py mark {job.key} skipped --note '...'")
    return 0


def cmd_draft(args, cfg, store):
    job = store.get(args.key)
    if not job:
        print(f"No job matches key {args.key!r}.")
        return 1
    provider = args.provider or cfg.get("draft_provider", "auto")
    path = make_draft(job, get_profile(), str(HERE / cfg.get("drafts_dir", "drafts")), provider)
    store.set_draft(job.key, path)
    print(f"draft → {path}")
    return 0


def cmd_open(args, cfg, store):
    job = store.get(args.key)
    if not job:
        print(f"No job matches key {args.key!r}.")
        return 1
    print(f"opening {job.url}")
    webbrowser.open(job.url)
    return 0


def cmd_mark(args, cfg, store):
    ok = store.set_status(args.key, args.status, note=args.note or "")
    print("ok" if ok else f"No job matches key {args.key!r}.")
    return 0 if ok else 1


def cmd_stats(args, cfg, store):
    counts = store.counts()
    order = ["new", "drafted", "applied", "interview", "offer", "rejected", "skipped"]
    total = sum(counts.values())
    print("Funnel:")
    for s in order:
        n = counts.get(s, 0)
        bar = "█" * min(n, 40)
        print(f"  {s:9} {n:4}  {bar}")
    print(f"  {'total':9} {total:4}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Personal assisted job-application funnel.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="pull repo README(s) into the tracker")
    s.add_argument("url", nargs="?", help="a single github repo URL (optional)")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("list", help="list tracked jobs")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--category", choices=CATEGORY_ORDER, help="swe | data-ml | pm | other")
    s.add_argument("--include-grad", action="store_true",
                   help="include advanced-degree (🎓 / PhD / MBA) roles")
    s.add_argument("--search", default="")
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("next", help="draft + open the next unapplied job")
    s.add_argument("--no-open", action="store_true", help="don't open the browser")
    s.add_argument("--category", choices=CATEGORY_ORDER,
                   help="only consider this category (swe | data-ml | pm | other)")
    s.add_argument("--include-grad", action="store_true",
                   help="include advanced-degree (🎓 / PhD / MBA) roles")
    s.add_argument("--provider", choices=["auto", "gemini", "claude", "template"],
                   help="override config's draft_provider for this run")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("draft", help="(re)generate a draft for one job")
    s.add_argument("key")
    s.add_argument("--provider", choices=["auto", "gemini", "claude", "template"],
                   help="override config's draft_provider for this run")
    s.set_defaults(fn=cmd_draft)

    s = sub.add_parser("open", help="open a job's link in the browser")
    s.add_argument("key")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("mark", help="set a job's status")
    s.add_argument("key")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("stats", help="show funnel counts")
    s.set_defaults(fn=cmd_stats)
    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = get_config()
    store = Store(str(HERE / cfg.get("db", "funnel.db")))
    return args.fn(args, cfg, store)


if __name__ == "__main__":
    sys.exit(main())
