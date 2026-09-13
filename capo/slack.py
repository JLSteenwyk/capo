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
        "In an objective thread use `followup: instructions`, `prepare OBJECTIVE_ID`, "
        "`approve OBJECTIVE_ID DIGEST`, or `sync OBJECTIVE_ID`. Commands require an @mention.")


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
        if type(settings.get("allow_publication", False)) is not bool:
            raise ValueError("allow_publication must be true or false")
        if settings.get("github_auth", "default") not in ("default", "keyring"):
            raise ValueError("Invalid GitHub authentication mode")
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
        self.pending_review = None
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS slack_deliveries (
                event_id TEXT PRIMARY KEY, data TEXT NOT NULL,
                next_chunk INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS slack_delivery_clock (
                channel TEXT PRIMARY KEY, not_before REAL NOT NULL
            );
        """)

    def reply(self, event, text):
        # Stay below Slack's truncation threshold, including escaped characters.
        for offset in range(0, len(text), 2500):
            self.client.chat_postMessage(channel=self.config["channel_id"],
                thread_ts=event.get("thread_ts", event["ts"]), text=html.escape(text[offset:offset + 2500]),
                mrkdwn=False, parse="none", link_names=False, unfurl_links=False, unfurl_media=False)

    def owns(self, objective):
        origin = objective.get("slack", {})
        return all(origin.get(key) == self.config[key] for key in ("team_id", "channel_id", "owner_user_id"))

    def objective(self, identifier):
        objective = self.store.get(identifier)
        if not self.owns(objective):
            raise ValueError("That objective does not belong to this Slack owner and channel")
        return objective

    def settings(self, objective):
        alias = objective.get("slack", {}).get("repository_alias")
        settings = self.config["repositories"].get(alias)
        if settings is None:
            matches = [value for value in self.config["repositories"].values()
                       if Path(value["path"]).resolve() == Path(objective["repo"]).resolve()]
            if len(matches) != 1:
                raise ValueError("Objective repository alias is no longer configured unambiguously")
            settings = matches[0]
        if Path(settings["path"]).resolve() != Path(objective["repo"]).resolve():
            raise ValueError("Repository alias changed since this objective was created")
        return settings

    def dispatch(self, event_id, body):
        from .cli import add_objective
        from .improvement import add_improvement

        self.pending_review = None
        if not authorized(self.config, body):
            raise ValueError("Unauthorized Slack request")
        event = body["event"]
        text = re.sub(r"^\s*<@[A-Z0-9]+>\s*", "", event["text"]).strip()
        if text.lower() == "help":
            return HELP
        match = re.fullmatch(r"(prepare|approve|sync)\s+([a-f0-9]{16})(?:\s+([a-f0-9]{64}))?", text)
        if match:
            from .github import GitHub, prepare, publish, remote_repository, sync
            from .repository import git
            command, identifier, digest = match.groups()
            objective = self.objective(identifier)
            settings = self.settings(objective)
            if not settings.get("allow_publication", False):
                raise ValueError("Slack publication is not enabled for this repository alias")
            if command == "prepare":
                if digest:
                    raise ValueError("prepare does not accept a digest")
                publication = prepare(self.store, identifier,
                    remote_repository(git(objective["repo"], "remote", "get-url", "origin")),
                    settings.get("publication_base", "main"))
                diff = (self.store.home / "artifacts" / identifier / "changes.patch").read_text()
                payload = publication["payload"]
                preview = (f"Draft PR preview for {identifier}\n"
                    f"Repository: {payload['repository']}\nBase: {payload['base_branch']}\n"
                    f"Commit: {payload['commit']}\nTitle: {payload['title']}\n\n"
                    f"{payload['body']}\nChanges:\n{diff}\n\n"
                    f"To approve this exact candidate: approve {identifier} {publication['digest']}")
                if len(preview) > 80000:
                    raise ValueError("Preview exceeds Slack review limit; inspect and publish using the CLI")
                self.pending_review = (identifier, publication["digest"])
                return preview
            if command == "approve":
                if not digest or objective.get("slack_review_digest") != digest:
                    raise ValueError("First request and review the full prepare preview, then approve its exact digest")
                result = publish(self.store, identifier, digest, GitHub(settings.get("github_auth", "default")))
                return f"Draft PR published: {result['pr']['url']}"
            if digest:
                raise ValueError("sync does not accept a digest")
            result = sync(self.store, identifier, GitHub(settings.get("github_auth", "default")))
            checks = result.get("statusCheckRollup") or []
            states = [str(check.get("conclusion") or check.get("state") or check.get("status", "unknown"))
                      for check in checks]
            return (f"{result['url']}: {result['state']}. Verified commit matches: "
                    f"{result['matches_verified_commit']}. CI: {', '.join(states) or 'No checks reported'}.")
        thread = event.get("thread_ts")
        if thread and not re.match(r"(?:status|cancel|help|prepare|approve|sync)(?:\s|$)", text):
            matches = [row for row in self.store.list() if self.owns(row)
                       and row["slack"].get("thread_ts") == thread]
            if len(matches) == 1:
                request = re.sub(r"^(?:followup|clarify):\s*", "", text, flags=re.I).strip()
                if not request:
                    raise ValueError("Follow-up instructions cannot be empty")
                self.store.add_followup(matches[0]["id"], event_id, request)
                return (f"Recorded your follow-up for {matches[0]['id']}. "
                        "Claude will incorporate it at the next safe checkpoint; existing execution limits remain.")
            if matches:
                raise ValueError("This thread contains multiple objectives; use a separate thread for each objective")
            if re.match(r"(?:followup|clarify):", text, re.I):
                raise ValueError("Send follow-up instructions in an existing objective thread")
        match = re.fullmatch(r"(status|cancel)\s+([a-f0-9]{16})", text)
        if match:
            command, identifier = match.groups()
            objective = self.objective(identifier)
            if command == "cancel":
                if self.store.request_cancellation(identifier, event_id):
                    from .runtime import exclusive
                    try:
                        with exclusive(self.store.home):
                            current = self.objective(identifier)
                            if current["status"] in ("queued", "awaiting_input", "blocked"):
                                current.update(status="cancelled", error="Cancelled by Slack owner")
                                self.store.save(current, "slack_cancelled")
                                return f"{identifier}: cancelled."
                    except ValueError:
                        pass  # The owning supervisor observes the durable request.
                    return (f"Cancellation requested for {identifier}. "
                            "The runner will confirm when it has stopped; interrupted runners require CLI recovery.")
            pr = objective.get("publication", {}).get("pr", {}).get("url", "")
            question = objective.get("question", "") if objective["status"] == "awaiting_input" else ""
            return f"{identifier}: {objective['status']}. Provider calls: {objective['calls']}. {pr} {question}".strip()
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
            objective["slack"].update(ts=event["ts"], thread_ts=event.get("thread_ts", event["ts"]),
                                      repository_alias=alias)
            self.store.save(objective, "slack_queued")
        return f"Queued {objective['id']} for {alias}. Claude Code will lead the work."

    def delivery_delay(self, seconds):
        with self.store.db:
            self.store.db.execute("INSERT INTO slack_delivery_clock VALUES (?,?) "
                "ON CONFLICT(channel) DO UPDATE SET not_before=excluded.not_before",
                (self.config["channel_id"], time.time() + seconds))

    def process_messages(self):
        for event_id, body in self.store.pending_slack():
            # Recheck policy after restart or configuration changes.
            if not authorized(self.config, body):
                self.store.finish_slack(event_id)
                continue
            row = self.store.db.execute("SELECT data,next_chunk FROM slack_deliveries WHERE event_id=?",
                                        (event_id,)).fetchone()
            if row is None:
                self.pending_review = None
                try:
                    text = self.dispatch(event_id, body)
                except (ValueError, RuntimeError, OSError) as exc:
                    text = f"Could not handle this request: {exc}"
                    self.pending_review = None
                data = {"text": text, "review": self.pending_review}
                self.pending_review = None
                with self.store.db:
                    self.store.db.execute("INSERT INTO slack_deliveries(event_id,data) VALUES (?,?)",
                                          (event_id, json.dumps(data)))
                index = 0
            else:
                data, index = json.loads(row[0]), row[1]
            review = data.get("review")
            if review and self.objective(review[0]).get("publication", {}).get("digest") != review[1]:
                data = {"text": "That candidate changed while the preview was being delivered. Request a new prepare preview.",
                        "review": None}
                index = 0
                with self.store.db:
                    self.store.db.execute("UPDATE slack_deliveries SET data=?,next_chunk=0 WHERE event_id=?",
                                          (json.dumps(data), event_id))
            chunks = [data["text"][offset:offset + 2500] for offset in range(0, len(data["text"]), 2500)]
            if index < len(chunks):
                clock = self.store.db.execute("SELECT not_before FROM slack_delivery_clock WHERE channel=?",
                                              (self.config["channel_id"],)).fetchone()
                if clock and time.time() < clock[0]:
                    continue
                try:
                    self.reply(body["event"], chunks[index])
                except Exception as exc:
                    # Slack SDK errors expose Retry-After through response.headers.
                    # Keep waiting in the event loop, never sleep through cancellation.
                    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                    delay = headers.get("Retry-After", headers.get("retry-after", 5))
                    if isinstance(delay, (list, tuple)):
                        delay = delay[0] if delay else 5
                    try:
                        delay = max(1, float(delay))
                    except (ValueError, TypeError):
                        delay = 5
                    self.delivery_delay(delay)
                    raise
                index += 1
                with self.store.db:
                    self.store.db.execute("UPDATE slack_deliveries SET next_chunk=? WHERE event_id=?",
                                          (index, event_id))
                self.delivery_delay(1)
            if index < len(chunks):
                continue
            if data.get("review"):
                identifier, digest = data["review"]
                # Serialize acknowledgement with follow-up invalidation.
                with self.store.db:
                    self.store.db.execute("BEGIN IMMEDIATE")
                    objective = self.objective(identifier)
                    if objective.get("publication", {}).get("digest") == digest:
                        objective["slack_review_digest"] = digest
                        self.store.save(objective, "slack_preview_delivered")
            self.store.finish_slack(event_id)

    def tick(self):
        self.process_messages()
        if self.active:
            objective = self.store.get(self.active_id)
            if self.active.poll() is not None:
                # The child may commit its terminal checkpoint between our first
                # read and poll. Never overwrite it using the earlier snapshot.
                objective = self.store.get(self.active_id)
                pending_followup = self.store.followups(objective["id"]) != objective.get("followups", [])
                if (objective["status"] not in ("completed", "blocked", "cancelled", "awaiting_input")
                        and not (objective["status"] == "queued" and pending_followup)
                        and objective.get("supervisor_pid", self.active.pid) == self.active.pid):
                    objective["status"] = "blocked"
                    objective["error"] = "Runner exited without a terminal checkpoint; inspect its artifacts"
                    self.store.save(objective, "slack_runner_failed")
                text = f"{self.active_id}: {objective['status']}."
                if objective["status"] == "completed":
                    text += " Verified changes are ready. Request prepare OBJECTIVE_ID to review a draft PR, or reply in this thread with follow-up instructions."
                elif objective["status"] == "awaiting_input":
                    text += f" Claude needs your input: {objective['question']} Reply here and mention the bot."
                elif objective["status"] == "queued" and pending_followup:
                    text += " Your follow-up is queued for Claude."
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
        # A restarted service must not launch a second runner during an existing
        # supervisor's queued checkpoint. The kernel lock is stronger evidence
        # than a saved PID, which may have been reused.
        from .runtime import exclusive
        try:
            with exclusive(self.store.home):
                pass
        except ValueError:
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
