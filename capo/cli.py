"""Local command line entry point."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .repository import git
from .github import GitHub, prepare, publish, sync
from .runtime import Runtime, exclusive
from .store import Store


def parser():
    root = argparse.ArgumentParser(description="Capo — development objectives led by Claude Code")
    root.add_argument("--version", action="version", version=f"capo {__version__}")
    root.add_argument("--team-name", default=os.environ.get("CAPO_TEAM_NAME", "SPARKITscience"),
                      help="Display name for this team (default: SPARKITscience)")
    root.add_argument("--home", type=Path, default=Path(os.environ.get(
        "CAPO_HOME", str(Path.home() / ".local/share/capo"))))
    root.add_argument("--github-auth", choices=("default", "keyring"), default="default",
                      help="Use normal gh authentication, or explicitly prefer its saved keyring login")
    root.add_argument("--providers-config", type=Path,
                      help="Private JSON transport configuration (or CAPO_PROVIDERS_CONFIG)")
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
    add.add_argument("--workers", nargs="+", choices=("codex", "grok"), default=["codex", "grok"])
    add.add_argument("--reviewer", choices=("auto", "claude", "codex", "grok"), default="auto")
    issue = commands.add_parser("issue", help="Read a GitHub issue into a local objective")
    issue.add_argument("number", type=int)
    issue.add_argument("--github", required=True, help="OWNER/REPO")
    issue.add_argument("--repo", type=Path, required=True)
    issue.add_argument("--check", action="append", required=True)
    issue.add_argument("--workers", nargs="+", choices=("codex", "grok"), default=["codex", "grok"])
    issue.add_argument("--reviewer", choices=("auto", "claude", "codex", "grok"), default="auto")
    issue.add_argument("--self-improvement", action="store_true",
                       help="Freeze Capo's regression suite for an issue about Capo itself")
    improve = commands.add_parser("improve", help="Queue a Capo improvement with frozen regression tests")
    improve.add_argument("request", nargs="?", default="Identify and implement one small reliability improvement in Capo.")
    improve.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    improve.add_argument("--workers", nargs="+", choices=("codex", "grok"), default=["codex", "grok"])
    improve.add_argument("--reviewer", choices=("auto", "claude", "codex", "grok"), default="auto")
    improve.add_argument("--max-calls", type=int, default=24)
    improve.add_argument("--max-rounds", type=int, default=3)
    improve.add_argument("--timeout", type=int, default=900)
    run = commands.add_parser("run", help="Run one objective in the foreground")
    run.add_argument("id")
    run.add_argument("--retry", action="store_true")
    draft = commands.add_parser("prepare", help="Prepare a verified commit and local PR preview")
    draft.add_argument("id")
    draft.add_argument("--github", required=True, help="OWNER/REPO matching source origin")
    draft.add_argument("--base", required=True, help="Target branch, e.g. main")
    draft.add_argument("--title")
    draft.add_argument("--body-file", type=Path,
                       help="Use exact PR text from a local file; may revise an unpublished prepared body")
    publication = commands.add_parser("publish", help="Push the prepared commit and create its draft PR")
    publication.add_argument("id")
    publication.add_argument("--digest", required=True, help="Exact digest from prepare")
    status = commands.add_parser("sync", help="Read published PR and CI status")
    status.add_argument("id")
    slack = commands.add_parser("slack", help="Run the owner-only Slack Socket Mode adapter")
    slack.add_argument("--config", type=Path, required=True)
    slack_setup = commands.add_parser("slack-setup", help="Resolve configured Slack workspace and channel names")
    slack_setup.add_argument("--config", type=Path, required=True)
    daemon = commands.add_parser("slack-daemon", help="Run Slack using a private token file")
    daemon.add_argument("--config", type=Path, required=True)
    daemon.add_argument("--env-file", type=Path, required=True)
    digest_settings = commands.add_parser("digest-settings", help="Inspect or configure the private morning digest")
    digest_settings.add_argument("--config", type=Path, required=True)
    digest_settings.add_argument("--time")
    digest_settings.add_argument("--artists-file", type=Path)
    enabled = digest_settings.add_mutually_exclusive_group()
    enabled.add_argument("--enable", action="store_true")
    enabled.add_argument("--pause", action="store_true")
    preview = commands.add_parser("digest-preview", help="Send one real digest preview to Slack")
    preview.add_argument("--config", type=Path, required=True)
    preview.add_argument("--env-file", type=Path, required=True)
    preview.add_argument("--id", required=True, help="Stable preview ID; reuse it to resume without duplication")
    gmail_auth = commands.add_parser("gmail-auth", help="Connect read-only Gmail inbox access")
    gmail_auth.add_argument("--client-secrets", type=Path, required=True)
    calendar_auth = commands.add_parser("calendar-auth", help="Connect a private Google Calendar account")
    calendar_auth.add_argument("--client-secrets", type=Path, required=True)
    commands.add_parser("calendar-check", help="Check Google Calendar access without changing events")
    browser = commands.add_parser("browser", help="Start a private browser task")
    browser.add_argument("request")
    browser.add_argument("--url", required=True)
    browser.add_argument("--allow-origin", action="append", required=True)
    browser_run = commands.add_parser("browser-run", help="Run a queued browser task")
    browser_run.add_argument("id")
    browser_run.add_argument("--headless", action="store_true")
    for name in ("browser-status", "browser-cancel", "browser-approve"):
        command = commands.add_parser(name)
        command.add_argument("id")
        if name == "browser-approve": command.add_argument("--digest", required=True)
    commands.add_parser("list", help="List durable objective states")
    for name in ("show", "events", "recover"):
        sub = commands.add_parser(name)
        sub.add_argument("id")
    return root


def add_objective(store, args, request, source=None, kind="development"):
    from .transport import load_config
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
    team_name = getattr(args, "team_name", "SPARKITscience")
    if not isinstance(team_name, str) or not team_name.strip() or len(team_name) > 64 or any(ord(c) < 32 for c in team_name):
        raise ValueError("Team name must be 1–64 characters without control characters")
    if source:
        for existing in store.list():
            if existing.get("source") == source and existing["repo"] == str(repo):
                return existing
    data = store.create({"request": request, "source": source, "repo": str(repo), "kind": kind,
                         "providers_config": load_config(getattr(args, "providers_config", None)),
                         "team_name": team_name,
                         "base": git(repo, "rev-parse", "HEAD"), "checks": checks,
                         "max_calls": getattr(args, "max_calls", 24),
                         "max_rounds": getattr(args, "max_rounds", 3),
                         "workers": getattr(args, "workers", ["codex", "grok"]),
                         "reviewer": getattr(args, "reviewer", "auto"),
                         "timeout": getattr(args, "timeout", 900)})
    data["workspace"] = str(store.home / "workspaces" / data["id"])
    store.save(data, "workspace_assigned")
    return data


def doctor(config_path=None):
    from .transport import load_config, health
    config = load_config(config_path)
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
    vm_ok = None
    if config.get("grok", {}).get("transport") == "lima":
        try:
            results["grok_vm"] = health(config["grok"])
            vm_ok = all(results["grok_vm"].get(key) for key in ("binary_ok", "bubblewrap", "login_file_present"))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            results["grok_vm"] = {"error": str(exc)}
            vm_ok = False
    print(json.dumps(results, indent=2))
    required = ("claude", "codex", "git") if vm_ok is not None else ("claude", "codex", "grok", "git")
    return 0 if all(results[name]["path"] for name in required) and vm_ok is not False else 1


def recover(store, objective_id):
    with exclusive(store.home):
        data = store.get(objective_id)
        if data["status"] != "running":
            raise ValueError("Only an interrupted running objective needs recovery")
        from .transport import reconcile
        for record in (store.home / "artifacts" / objective_id).rglob("remote.json"):
            if not record.with_name("remote-exit.json").exists():
                status = reconcile(record)
                record.with_name("remote-exit.json").write_text(json.dumps(status))
        from .process import reconcile_local
        for record in (store.home / "artifacts" / objective_id).rglob("process.json"):
            reconcile_local(record.parent)
        data["status"] = "blocked"
        data["error"] = "Recovered interrupted run; inspect workspace and use run --retry"
        store.save(data, "recovered")
        return data


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command.startswith("digest-"):
            if args.providers_config:
                os.environ["CAPO_PROVIDERS_CONFIG"] = str(args.providers_config.expanduser().resolve())
            from .digest_cli import command
            return command(args)
        if args.command == "slack-daemon":
            from .digest_cli import load_slack_environment
            load_slack_environment(args.env_file)
            args.command = "slack"
        if args.command == "gmail-auth":
            from .gmail import authorize
            try:
                authorize(args.client_secrets.expanduser())
            except Exception:
                raise RuntimeError("Gmail sign-in failed. Check the private client file and Google OAuth setup.") from None
            print("Gmail connected. Enable gmail in your private Slack configuration.")
            return 0
        if args.command == "calendar-auth":
            from .calendar import authorize
            try:
                authorize(args.client_secrets.expanduser())
            except Exception:
                raise RuntimeError("Calendar sign-in failed. Check the private client file and Google OAuth setup.") from None
            print("Google Calendar connected. You can now enable calendar support in your private Slack config.")
            return 0
        if args.command == "calendar-check":
            from .calendar import GoogleCalendar
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            try:
                GoogleCalendar().events(now.isoformat(), (now + timedelta(days=1)).isoformat())
            except Exception:
                raise RuntimeError("Calendar access failed. Check the connection with calendar-auth.") from None
            print("Google Calendar access works. No events were changed.")
            return 0
        if args.command == "doctor":
            return doctor(args.providers_config)
        if args.command == "slack":
            from .slack import serve
            if args.providers_config:
                from .transport import load_config
                load_config(args.providers_config)
                os.environ["CAPO_PROVIDERS_CONFIG"] = str(args.providers_config.expanduser().resolve())
            serve(args.home, args.config)
            return 0
        if args.command == "slack-setup":
            from .slack import configure
            print(json.dumps(configure(args.config), indent=2))
            return 0
        if args.command.startswith("browser"):
            from . import browser
            if args.command == "browser":
                result = browser.create(args.home, args.request, args.url, args.allow_origin)
            elif args.command == "browser-run":
                result = browser.run(args.home, args.id, headless=args.headless)
            elif args.command == "browser-approve":
                browser.approve(args.home, args.id, args.digest)
                result = {"status": "approval recorded"}
            elif args.command == "browser-cancel":
                browser.cancel(args.home, args.id)
                result = {"status": "cancellation requested"}
            else:
                result = browser.read(args.home, args.id)
            print(json.dumps(result, indent=2))
            return 0
        store = Store(args.home)
        if args.command == "add":
            result = add_objective(store, args, args.request)
        elif args.command == "improve":
            from .improvement import add_improvement
            result = add_improvement(store, args)
        elif args.command == "issue":
            issue = GitHub(args.github_auth).issue(args.github, args.number)
            request = f"{issue['title']}\n\n{issue['body']}"
            if args.self_improvement:
                from .improvement import add_improvement
                args.request, args.source = request, issue["url"]
                result = add_improvement(store, args)
            else:
                result = add_objective(store, args, request, issue["url"])
        elif args.command == "run":
            result = Runtime(store).run(args.id, retry=args.retry)
        elif args.command == "list":
            result = [{key: row[key] for key in ("id", "status", "request", "calls")}
                      for row in store.list()]
        elif args.command == "show":
            result = store.get(args.id)
        elif args.command == "events":
            result = store.events(args.id)
        elif args.command == "prepare":
            body = args.body_file.read_text() if args.body_file is not None else None
            result = prepare(store, args.id, args.github, args.base, args.title, body=body)
        elif args.command == "publish":
            result = publish(store, args.id, args.digest, GitHub(args.github_auth))
        elif args.command == "sync":
            result = sync(store, args.id, GitHub(args.github_auth))
        else:
            result = recover(store, args.id)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Cancelled; state and artifacts retained.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"capo: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.db.close()
