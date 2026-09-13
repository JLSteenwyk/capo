"""Local command line entry point."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .repository import git
from .runtime import Runtime, exclusive
from .store import Store


def parser():
    root = argparse.ArgumentParser(description="Boardroom — development objectives led by Claude Code")
    root.add_argument("--home", type=Path, default=Path(os.environ.get(
        "BOARDROOM_HOME", str(Path.home() / ".local/share/boardroom"))))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check executables without invoking models")
    add = commands.add_parser("add", help="Queue a local development objective")
    add.add_argument("request")
    add.add_argument("--repo", type=Path, required=True)
    add.add_argument("--check", action="append", required=True,
                     help="Trusted verification command, parsed as argv (no shell); repeatable")
    add.add_argument("--max-calls", type=int, default=24)
    add.add_argument("--max-rounds", type=int, default=3)
    add.add_argument("--timeout", type=int, default=900)
    issue = commands.add_parser("issue", help="Read a GitHub issue into a local objective")
    issue.add_argument("number", type=int)
    issue.add_argument("--github", required=True, help="OWNER/REPO")
    issue.add_argument("--repo", type=Path, required=True)
    issue.add_argument("--check", action="append", required=True)
    run = commands.add_parser("run", help="Run one objective in the foreground")
    run.add_argument("id")
    run.add_argument("--retry", action="store_true")
    commands.add_parser("list", help="List durable objective states")
    for name in ("show", "events", "recover"):
        sub = commands.add_parser(name)
        sub.add_argument("id")
    return root


def add_objective(store, args, request, source=None):
    repo = args.repo.expanduser().resolve()
    repo = Path(git(repo, "rev-parse", "--show-toplevel"))
    if git(repo, "status", "--porcelain"):
        raise ValueError("Commit or stash repository changes before taking an objective snapshot")
    checks = [shlex.split(command) for command in args.check]
    if not all(checks):
        raise ValueError("Verification commands cannot be empty")
    for field in ("max_calls", "max_rounds", "timeout"):
        if getattr(args, field, 1) < 1:
            raise ValueError(f"{field} must be positive")
    if not request.strip():
        raise ValueError("Objective cannot be empty")
    if source:
        for existing in store.list():
            if existing.get("source") == source and existing["repo"] == str(repo):
                return existing
    data = store.create({"request": request, "source": source, "repo": str(repo),
                         "base": git(repo, "rev-parse", "HEAD"), "checks": checks,
                         "max_calls": getattr(args, "max_calls", 24),
                         "max_rounds": getattr(args, "max_rounds", 3),
                         "timeout": getattr(args, "timeout", 900)})
    data["workspace"] = str(store.home / "workspaces" / data["id"])
    store.save(data, "workspace_assigned")
    return data


def doctor():
    results = {}
    for tool in ("claude", "codex", "grok", "gh", "git"):
        executable = shutil.which(tool)
        version = None
        if executable:
            try:
                version = subprocess.run([executable, "--version"], capture_output=True,
                                         text=True, timeout=10).stdout.strip().splitlines()[0]
            except (OSError, subprocess.TimeoutExpired, IndexError):
                version = "version check failed"
        results[tool] = {"path": executable, "version": version}
    results["note"] = ("Presence does not verify authentication or remaining subscription quota. "
                       "Provider configuration can override authentication; verify it before live runs.")
    print(json.dumps(results, indent=2))
    return 0 if all(results[name]["path"] for name in ("claude", "codex", "grok", "git")) else 1


def recover(store, objective_id):
    with exclusive(store.home):
        data = store.get(objective_id)
        if data["status"] != "running":
            raise ValueError("Only an interrupted running objective needs recovery")
        for record in (store.home / "artifacts" / objective_id).rglob("process.json"):
            if record.with_name("exit.json").exists():
                continue
            pid = json.loads(record.read_text())["pid"]
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise ValueError(f"Process {pid} may still be active; inspect it before recovery")
        data["status"] = "blocked"
        data["error"] = "Recovered interrupted run; inspect workspace and use run --retry"
        store.save(data, "recovered")
        return data


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor()
        store = Store(args.home)
        if args.command == "add":
            result = add_objective(store, args, args.request)
        elif args.command == "issue":
            if args.number < 1 or len(args.github.split("/")) != 2 or args.github.startswith("-"):
                raise ValueError("Expected a positive issue number and OWNER/REPO")
            response = subprocess.run(["gh", "issue", "view", str(args.number), "--repo", args.github,
                                       "--json", "title,body,url"], check=True, capture_output=True,
                                      text=True, timeout=30)
            issue = json.loads(response.stdout)
            result = add_objective(store, args, f"{issue['title']}\n\n{issue['body']}", issue["url"])
        elif args.command == "run":
            result = Runtime(store).run(args.id, retry=args.retry)
        elif args.command == "list":
            result = [{key: row[key] for key in ("id", "status", "request", "calls")}
                      for row in store.list()]
        elif args.command == "show":
            result = store.get(args.id)
        elif args.command == "events":
            result = store.events(args.id)
        else:
            result = recover(store, args.id)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Cancelled; state and artifacts retained.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"boardroom: {exc}", file=sys.stderr)
        return 1
