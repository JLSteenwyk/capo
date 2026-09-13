# Human communication through Slack

Capo's primary conversational interface is Slack; the CLI remains available for setup, recovery, and explicit publication. This installation uses **#capo in the SPARKITscience workspace**, accepts objectives **only from its owner**, and displays **SPARKITscience** as the configurable team identity. The owner's supplied member ID is saved in private local configuration rather than committed to this repository.

The adapter uses Slack Socket Mode, so the agent computer opens the connection to Slack without a public webhook server. [Slack Socket Mode documentation](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/).

## Configure

1. Create an app in the SPARKITscience workspace using [the supplied manifest](../integrations/slack/manifest.json). Customize both display-name fields if you want a different team identity.
2. Install it to the workspace and invite it to #capo. The bot uses `app_mentions:read` and `chat:write` for the interface, plus `channels:read` and `groups:read` to resolve the configured public or private channel during setup. It does not request channel message-history scopes.
3. Generate an app-level token with `connections:write`, and keep it in `SLACK_APP_TOKEN` on the agent computer. Put the installed bot token in `SLACK_BOT_TOKEN`. Keep both outside source control and chat messages.
4. Copy [the configuration example](../integrations/slack/config.example.json) to a private location outside the repository. Set the owner ID, workspace/channel names, and repository path. `slack-setup` resolves and pins the workspace/channel IDs using the bot token; it sends no messages. IDs are authoritative during operation, and the service refuses to run before they are configured.
5. Install the optional adapter and start it:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[slack]'
.venv/bin/capo slack-setup --config /private/path/to/slack-config.json
.venv/bin/capo slack --config /private/path/to/slack-config.json
```

Use the same `CAPO_HOME` as your CLI. The service checks that its bot token belongs to the configured workspace. Startup performs Slack authentication and opens a real connection; no connection or message is sent by merely installing the package or running tests.

The sample uses Claude and Codex explicitly because of the documented Grok sandbox failure on the current Mac. On a compatible host, configure `"workers": ["codex", "grok"]` and `"reviewer": "auto"` for the full team. Capo does not silently replace an unavailable provider.

## Use

Mention the bot in the configured channel:

```text
@SPARKITscience capo: Fix the issue intake error message when GitHub authentication fails.
@SPARKITscience improve capo: Identify and fix one small reliability problem.
@SPARKITscience status OBJECTIVE_ID
@SPARKITscience cancel OBJECTIVE_ID
@SPARKITscience help
```

The repository alias selects a locally configured checkout and trusted verification commands. Text after the colon is the objective. Messages cannot set arbitrary paths, verification commands, provider credentials, or permission policy. Self-improvement must be enabled for the alias.

The bot replies in the originating thread with an objective ID, stage updates, and a terminal status. It starts one queued Slack objective at a time. To queue without starting automatically, set `auto_run` to false and use `capo run` from the CLI. Completed changes can be prepared and published using the normal CLI; subsequent Slack `status` responses include the recorded PR URL.

Cancellation stops a queued objective or the active child owned by this service. An uncertain running objective after a service crash requires CLI inspection and recovery. A stopped service terminates its active child when it receives Ctrl-C or SIGTERM. The service never automatically retries blocked, cancelled, or uncertain running work.

## Identity and delivery behavior

Authorization happens before an incoming event enters the queue and again before dispatch. Workspace ID, channel ID, owner ID, event type, and bot/subtype checks must all pass. Unauthorized users receive no response and create no objectives. Only owner-originated objectives from this configured channel can be inspected or cancelled through this adapter.

Incoming Slack event IDs are persisted and deduplicated. A repeated event cannot create a second objective. A crash between dispatch and sending a reply can produce a duplicate status message on retry; there is no claim of exactly-once Slack notification delivery. Source code and raw logs are not automatically uploaded to Slack. Slack tokens are removed from child worker and verification environments.

## Later communication features

The current interface supports goal intake, status, cancellation, and a thread for updates. Freeform follow-up instructions, clarification questions, approval buttons, artifact uploads, scheduled briefings, and DMs are not implemented. Add them through the same identity checks and durable state contracts. Publishing or merging through Slack must bind to an inspectable exact candidate, just like the CLI publication path.

Slack app details follow the official [Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/) and [app mention](https://docs.slack.dev/reference/events/app_mention/) interfaces. A live workspace connection remains pending configuration and credentials.
