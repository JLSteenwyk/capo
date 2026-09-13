"""Socket Mode control surface. Only the configured Slack owner can queue work."""

import argparse
import fcntl
import html
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .store import Store


HELP = ("Send `repo-alias: your objective` to queue work, or `improve repo-alias: your objective` "
        "for Capo self-improvement. Use `status OBJECTIVE_ID`, `cancel OBJECTIVE_ID`, or `help`. "
        "Commands require an @mention. PR publication remains an explicit CLI action.")


def configure(config_path, client=None):
    """Resolve user-selected workspace/channel names through read-only Slack calls."""
    config = json.loads(config_path.read_text())
    if not config.get("workspace_name") or not config.get("channel_name"):
        raise ValueError("Configure workspace_name and channel_name first")
    if client is None:
        if not os.environ.get("SLACK_BOT_TOKEN"):
            raise ValueError("Set SLACK_BOT_TOKEN locally to resolve the Slack workspace and channel IDs")
        try:
            from slack_sdk import WebClient
        except ImportError:
            raise ValueError("Install Slack support with: python3 -m pip install -e '.[slack]'") from None
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    identity = client.auth_test()
    if identity.get("team", "").casefold() != config["workspace_name"].casefold():
        raise ValueError("Slack token belongs to a different named workspace")
    if config.get("team_id") and config["team_id"] != identity["team_id"]:
        raise ValueError("Refusing to replace a configured workspace ID")
    cursor = None
    found = None
    for _ in range(100):
        response = client.conversations_list(types="public_channel,private_channel", exclude_archived=True,
                                              limit=200, cursor=cursor)
        found = next((channel for channel in response["channels"]
                      if channel["name"] == config["channel_name"].lstrip("#")), None)
        if found:
            break
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    if not found or not found.get("is_member"):
        raise ValueError("Invite the bot to the configured channel, then run slack-setup again")
    if config.get("channel_id") and config["channel_id"] != found["id"]:
        raise ValueError("Refusing to replace a configured channel ID")
    config.update(team_id=identity["team_id"], channel_id=found["id"])
    validate_config(config)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, config_path)
    return {key: config[key] for key in ("workspace_name", "team_id", "channel_name", "channel_id", "owner_user_id")}


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Slack configuration must be an object")
    if type(config.get("auto_run", True)) is not bool:
        raise ValueError("auto_run must be true or false")
    for key, prefix in (("team_id", "T"), ("channel_id", "CG"), ("owner_user_id", "UW")):
        if not isinstance(config.get(key), str) or not re.fullmatch(f"[{prefix}][A-Z0-9]+", config[key]):
            raise ValueError(f"Slack config needs a valid {key}")
    repos = config.get("repositories")
    if not isinstance(repos, dict) or not repos:
        raise ValueError("Configure at least one named repository")
    for alias, settings in repos.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", alias) or not isinstance(settings, dict):
            raise ValueError("Invalid repository alias/configuration")
        if not Path(settings.get("path", "")).is_absolute():
            raise ValueError("Repository paths must be absolute")
        checks = settings.get("checks")
        if not isinstance(checks, list) or not checks or not all(isinstance(c, str) and c.strip() for c in checks):
            raise ValueError("Each repository requires trusted check commands")
        workers = settings.get("workers", ["codex", "grok"])
        if not isinstance(workers, list) or not workers or any(w not in ("codex", "grok") for w in workers):
            raise ValueError("Invalid implementation workers")
        if settings.get("reviewer", "auto") not in ("auto", "claude", "codex", "grok"):
            raise ValueError("Invalid reviewer")
        for name in ("max_calls", "max_rounds", "timeout"):
            value = settings.get(name, 1)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(settings.get("allow_self_improvement", False)) is not bool:
            raise ValueError("allow_self_improvement must be true or false")
    return config


def authorized(config, body):
    event = body.get("event", {})
    return (body.get("team_id") == config["team_id"]
            and event.get("channel") == config["channel_id"]
            and event.get("user") == config["owner_user_id"]
            and event.get("type") == "app_mention"
            and not event.get("bot_id") and not event.get("subtype"))


def ingest(home, config, body):
    if not authorized(config, body) or not body.get("event_id"):
        return False
    event = body["event"]
    if not isinstance(event.get("text"), str) or len(event["text"]) > 8000 or not event.get("ts"):
        return False
    store = Store(home)
    try:
        store.enqueue_slack(body["event_id"], body)
    finally:
        store.db.close()
    return True


class SlackService:
    def __init__(self, store, config, client):
        self.store, self.config, self.client = store, validate_config(config), client
        self.active = None
        self.active_id = None
        self.last_stage = None

    def reply(self, event, text):
        self.client.chat_postMessage(channel=self.config["channel_id"],
            thread_ts=event.get("thread_ts", event["ts"]), text=html.escape(text),
            mrkdwn=False, parse="none", link_names=False, unfurl_links=False, unfurl_media=False)

    def owns(self, objective):
        origin = objective.get("slack", {})
        return all(origin.get(key) == self.config[key] for key in ("team_id", "channel_id", "owner_user_id"))

    def objective(self, identifier):
        objective = self.store.get(identifier)
        if not self.owns(objective):
            raise ValueError("That objective does not belong to this Slack owner and channel")
        return objective

    def dispatch(self, event_id, body):
        from .cli import add_objective
        from .improvement import add_improvement

        event = body["event"]
        text = re.sub(r"^\s*<@[A-Z0-9]+>\s*", "", event["text"]).strip()
        if text.lower() == "help":
            return HELP
        match = re.fullmatch(r"(status|cancel)\s+([a-f0-9]{16})", text)
        if match:
            command, identifier = match.groups()
            objective = self.objective(identifier)
            if command == "cancel":
                if self.active_id == identifier and self.active and self.active.poll() is None:
                    self.active.terminate()
                    return f"Stopping objective {identifier}."
                if objective["status"] == "queued":
                    objective["status"] = "cancelled"
                    objective["error"] = "Cancelled by Slack owner"
                    self.store.save(objective, "slack_cancelled")
                elif objective["status"] == "running":
                    return "This runner is not owned by the current Slack service; inspect it from the CLI."
            pr = objective.get("publication", {}).get("pr", {}).get("url", "")
            return f"{identifier}: {objective['status']}. Provider calls: {objective['calls']}. {pr}".strip()
        match = re.fullmatch(r"(?:(improve)\s+)?([a-zA-Z0-9_-]+):\s*(.+)", text, re.DOTALL)
        if not match:
            return HELP
        improve, alias, request = match.groups()
        if alias not in self.config["repositories"]:
            raise ValueError("Unknown repository alias")
        settings = self.config["repositories"][alias]
        source = f"slack:{self.config['team_id']}:{event_id}"
        existing = next((row for row in self.store.list() if row.get("source") == source), None)
        if existing:
            objective = existing
        else:
            args = argparse.Namespace(repo=Path(settings["path"]), request=request,
                check=settings["checks"], workers=settings.get("workers", ["codex", "grok"]),
                reviewer=settings.get("reviewer", "auto"), max_calls=settings.get("max_calls", 24),
                max_rounds=settings.get("max_rounds", 3), timeout=settings.get("timeout", 900),
                team_name=self.config.get("team_name", "SPARKITscience"))
            if improve:
                if not settings.get("allow_self_improvement", False):
                    raise ValueError("Self-improvement is not enabled for this repository alias")
                args.source = source
                objective = add_improvement(self.store, args)
            else:
                objective = add_objective(self.store, args, request, source=source)
        if "slack" not in objective:
            objective["slack"] = {key: self.config[key] for key in ("team_id", "channel_id", "owner_user_id")}
            objective["slack"].update(ts=event["ts"], thread_ts=event.get("thread_ts", event["ts"]))
            self.store.save(objective, "slack_queued")
        return f"Queued {objective['id']} for {alias}. Claude Code will lead the work."

    def process_messages(self):
        for event_id, body in self.store.pending_slack():
            # Recheck policy after restart or configuration changes.
            if authorized(self.config, body):
                try:
                    text = self.dispatch(event_id, body)
                except (ValueError, RuntimeError, OSError) as exc:
                    text = f"Could not handle this request: {exc}"
                self.reply(body["event"], text)
            self.store.finish_slack(event_id)

    def tick(self):
        self.process_messages()
        if self.active:
            objective = self.store.get(self.active_id)
            if self.active.poll() is not None:
                if (objective["status"] not in ("completed", "blocked", "cancelled")
                        and objective.get("supervisor_pid", self.active.pid) == self.active.pid):
                    objective["status"] = "blocked"
                    objective["error"] = "Runner exited without a terminal checkpoint; inspect its artifacts"
                    self.store.save(objective, "slack_runner_failed")
                text = f"{self.active_id}: {objective['status']}."
                if objective["status"] == "completed":
                    text += " Verified changes and a delivery report are ready. Use the CLI to prepare a draft PR."
                else:
                    text += " Inspect the objective's CLI status and artifacts for details."
                self.reply(objective["slack"], text)
                self.active, self.active_id, self.last_stage = None, None, None
            elif objective.get("active_stage") != self.last_stage:
                self.last_stage = objective.get("active_stage")
                if self.last_stage:
                    self.reply(objective["slack"], f"{self.active_id}: {self.last_stage} in progress.")
            return
        if not self.config.get("auto_run", True):
            return
        # Oldest first. Never restart an uncertain running or blocked objective automatically.
        for objective in reversed(self.store.list()):
            if objective["status"] == "queued" and self.owns(objective):
                directory = self.store.home / "artifacts" / objective["id"]
                directory.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                env.pop("SLACK_APP_TOKEN", None)
                env.pop("SLACK_BOT_TOKEN", None)
                with (directory / "runner.log").open("a") as output:
                    self.active = subprocess.Popen([sys.executable, "-m", "capo", "--home", str(self.store.home),
                        "run", objective["id"]], cwd=Path(__file__).resolve().parent.parent,
                        env=env, stdout=output, stderr=output, start_new_session=True)
                self.active_id = objective["id"]
                return


def serve(home, config_path):
    config = validate_config(json.loads(config_path.read_text()))
    for variable in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"):
        if not os.environ.get(variable):
            raise ValueError(f"Set {variable} locally before starting the Slack adapter")
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
        from slack_sdk.errors import SlackApiError, SlackClientError
    except ImportError:
        raise ValueError("Install Slack support with: python3 -m pip install -e '.[slack]'") from None
    store = Store(home)
    with (store.home / "slack-service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A Slack service already owns this state directory") from None
        app = App(token=os.environ["SLACK_BOT_TOKEN"])
        identity = app.client.auth_test()
        if identity["team_id"] != config["team_id"]:
            raise ValueError("Slack token belongs to a different workspace")
        @app.event("app_mention")
        def mention(body):
            ingest(store.home, config, body)
        service = SlackService(store, config, app.client)
        handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
        previous = signal.getsignal(signal.SIGTERM)
        def stop(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        try:
            handler.connect()
            while True:
                try:
                    service.tick()
                except (SlackApiError, SlackClientError):
                    print("Slack request failed; retrying after a short delay.", file=sys.stderr)
                    time.sleep(5)
                time.sleep(1)
        finally:
            signal.signal(signal.SIGTERM, previous)
            handler.close()
            if service.active and service.active.poll() is None:
                service.active.terminate()
                try:
                    service.active.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    service.active.kill()
                    service.active.wait()
            store.db.close()
