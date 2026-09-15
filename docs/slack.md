# Human communication through Slack

Capo's primary conversational interface is Slack; the CLI remains available for setup and recovery. This installation uses **#capo in the SPARKITscience workspace**, accepts objectives **only from its owner**, and displays **SPARKITscience** as the configurable team identity. The owner's supplied member ID is saved in private local configuration rather than committed to this repository.

The adapter uses Slack Socket Mode, so the agent computer opens the connection to Slack without a public webhook server. [Slack Socket Mode documentation](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/).

## Configure

1. Create an app in the SPARKITscience workspace using [the supplied manifest](../integrations/slack/manifest.json). The app and bot are named `capo`; customize both display-name fields to rename the bot independently of the team identity.
2. Install it to the workspace and invite it to #capo. The bot uses `app_mentions:read` and `chat:write` for the interface, plus `channels:read` and `groups:read` to resolve the configured public or private channel during setup. It also uses `channels:history` with `message.channels` for untagged replies in public channels, and `groups:history` with `message.groups` for private channels. Only owner replies in already-known Capo threads are accepted; unrelated messages are discarded.
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

The sample explicitly selects Claude and Codex. After configuring and verifying the Grok Linux VM transport described in the Grok setup documentation, configure `"workers": ["codex", "grok"]` and `"reviewer": "auto"` for the full team. Capo does not silently replace an unavailable provider.

## Rename an existing app

In the [Slack app dashboard](https://api.slack.com/apps), select the existing app and open **App Manifest**. Set `display_information.name` and `features.bot_user.display_name` to `capo`, then save. Change only those name fields; preserve the app permissions and event settings. Slack propagates these identity changes to existing installations without reinstallation. The private workspace and owner configuration remain the same. See [Slack identity propagation](https://docs.slack.dev/app-management/distribution/).

## Natural-language conversation

Mention the bot and speak normally, for example:

```text
@capo, can you check if PhyKIT has any issues that need attention?
@capo please fix this issue in PhyKIT: https://github.com/OWNER/REPO/issues/123
@capo how is it going?
@capo show me the proposed changes
```

Claude interprets the message and selects a configured repository and supported action. An issue question reads current open issues without starting development. A clear request to implement work queues the original instructions with trusted repository checks. In an objective thread, progress questions read status while new instructions become follow-ups. Ambiguous requests get a clarification. Recent messages in the same thread provide limited conversational context.

Interpretation uses one bounded Claude subscription call per new natural-language message, runs in the background, and persists its result. Explicit commands remain available without that call. An interrupted interpretation is not automatically retried; resend the message if asked. Repository and owner permissions are checked before interpretation and again before acting. Publication requires an explicit approve command after the short review message. In the same thread, the approval code can be omitted; unrelated unrelated conversational wording cannot authorize delivery.

## Use

Mention the bot in the configured channel:

```text
@capo capo: Fix the issue intake error message when GitHub authentication fails.
@capo improve capo: Identify and fix one small reliability problem.
@capo status OBJECTIVE_ID
@capo cancel OBJECTIVE_ID
@capo prepare OBJECTIVE_ID
@capo approve OBJECTIVE_ID EXACT_DIGEST_FROM_PREVIEW
@capo sync OBJECTIVE_ID
@capo help
```

The repository alias selects a locally configured checkout and trusted verification commands. GitHub issue URLs in objectives or follow-ups are read through the configured GitHub login, and their titles and bodies are passed to Claude as task context. Only issues from the selected checkout’s GitHub origin are accepted (at most three, each limited to 50,000 characters). Slack-formatted links are supported. Other URLs are not fetched; provide their relevant contents explicitly. Failed issue retrieval leaves the request unqueued so it can be retried after access is restored. Text after the colon is the objective. Messages cannot set arbitrary paths, verification commands, provider credentials, or permission policy. Self-improvement must be enabled for the alias.

The bot acknowledges work in the originating thread, shares one short plan, and sends the final result. Internal implementation, test, review, and revision stages do not generate notifications. It still asks when indispensable input is missing, and status is available on request. Blocked results explain known causes such as incompatible worker/reviewer assignments or exhausted revision limits; raw paths, credentials, and provider diagnostics remain private. It starts one queued Slack objective at a time. To queue without starting automatically, set `auto_run` to false and use `capo run` from the CLI. Completed changes can be reviewed and published through Slack when the repository alias has `allow_publication: true`. The default is false. `publication_base` selects the target branch (default `main`), and `github_auth` selects `default` or `keyring`; neither can be changed through messages. `prepare OBJECTIVE_ID` sends a short review message with the change title, target repository/branch, and exact approval command. `details OBJECTIVE_ID` shows the full diff, PR body, and commit on request. Nothing is pushed by preparation. With automatic routine delivery disabled, only after the short review message has been delivered does `approve OBJECTIVE_ID DIGEST` authorize pushing that exact verified commit and creating its draft PR. Merging remains off unless the owner enables `merge_after_approval`. Oversized details require CLI review. `sync OBJECTIVE_ID` reads PR and CI state; `status` includes the recorded PR URL.

Capo also accepts a copied Slack mention-and-command enclosed in bold formatting (`*<@BOT_ID> help*`); it strips only the enclosing bold formatting before parsing the mention command normally. For an objective whose publication status is `published`, `prepare` or `approve` first reads the PR's current GitHub state through `sync`. If the state is `MERGED` or `CLOSED`, Capo replies in the requesting thread with the PR's state and URL and does not prepare or publish again.

Reply in an objective's original thread to clarify or change instructions; a mention is optional there:

```text
@capo clarify: Keep the existing public function signature.
@capo Also cover an empty input in the tests.
```

Follow-ups are stored independently of worker checkpoints, deduplicated by event ID, and incorporated by Claude at a safe boundary. New input invalidates an unpublished completed candidate's approval and resumes work in the same candidate checkout, preserving previous changes and cumulative execution limits. Blocked/cancelled work can also receive follow-ups; exhausted limits still require explicit operator reconciliation. Follow-ups cannot alter trusted check commands or policy. Once publication has started, create a new objective after integrating the published changes. This avoids silently amending an approved PR.

Start a conversation with an @mention. After that, owner replies in the same thread do not need a mention. When Claude cannot plan without indispensable information, it pauses the objective in `awaiting_input` and sends a specific question to the thread. Reply in that thread to resume planning in the same checkout. Waiting does not consume additional worker calls; the original limits still apply. `status` repeats the pending question, and `cancel` can stop a waiting objective.

Cancellation persists a request that the owning supervisor observes, including after a Slack service restart. The bot reports cancellation as requested until the runner confirms it has stopped; an offline queued objective can be cancelled immediately. A restarted service checks the supervisor’s kernel lock before launching work, including when a surviving runner is at a queued checkpoint. It does not adopt or kill a process based solely on a saved PID. An uncertain running objective after a service crash requires CLI inspection and recovery; terminal statuses and clarification questions are rediscovered and delivered when Slack reconnects. SIGKILL cannot run cleanup, so inspect tracked local and remote processes before recovery. A stopped service terminates its active child when it receives Ctrl-C or SIGTERM. The service never automatically retries blocked, cancelled, or uncertain running work without new owner instructions.

## Identity and delivery behavior

Authorization happens before an incoming event enters the queue and again before dispatch. Workspace ID, channel ID, owner ID, event type, and bot/subtype checks must all pass. Unauthorized users receive no response and create no objectives. Only owner-originated objectives from this configured channel can be inspected, followed up, cancelled, or published through this adapter.

Terminal and clarification notifications persist delivery progress independently of the live child handle. Restarting the service discovers undelivered terminal checkpoints, including objectives run from the CLI. Notifications use the same channel pacing and Retry-After handling as previews. Owner/channel/workspace policy is rechecked, and a follow-up supersedes a question that has not finished delivery. Already acknowledged chunks are not repeated on an ordinary restart; a crash after Slack accepts a chunk but before its local acknowledgement can still repeat that chunk. Plan delivery is deduplicated across restarts; internal stage changes are silent.

Incoming Slack event IDs are persisted and deduplicated. A repeated event cannot create a second objective. A crash between dispatch and sending a reply can produce a duplicate status message on retry; there is no claim of exactly-once Slack notification delivery. Source code is sent only when the owner explicitly requests a publication preview. Raw logs are not automatically uploaded to Slack. Slack tokens are removed from child worker and verification environments.

## Current limits

Changes outside the automatic routine policy use explicit approval commands rather than buttons. Scheduled briefings, DMs, and file uploads are not implemented. Requested code details are split into bounded messages with persisted progress and channel pacing. Rate limits defer delivery using Retry-After, and restarts resume the remaining chunks. A delivery failure leaves approval disabled until the short review message succeeds; changed candidates invalidate an outstanding preview. Notification delivery can repeat after a crash, while objective creation and follow-up ingestion are deduplicated. Publication uses the same immutable verification and remote reconciliation gates as the CLI.

Slack app details follow the official [Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode/) and [app mention](https://docs.slack.dev/reference/events/app_mention/) interfaces. Automated tests use fake Slack and GitHub clients; a real workspace connection must be verified separately after local credentials are configured.

## Automatic delivery of routine work

Set both `allow_publication: true` and `auto_publish_routine: true` on a trusted repository alias to authorize automatic draft PR delivery. `auto_publish_routine` defaults to false. The configured owner can then request a small fix in natural language; Capo implements it, runs checks, obtains independent review and Claude acceptance, and publishes the exact accepted tree without a separate preview/approval exchange. Completion includes the PR link. This policy does not authorize merging.

Eligibility is conservative: at most three files, existing Python function bodies with unchanged definitions/imports/module behavior, tests, and Markdown. Changed-line limits are 80 source, 250 test, and 80 documentation lines. New source functions/interfaces/files, configuration, protected Capo components, nonregular files, and suspected private content require review. These structural checks do not prove semantic safety; the existing verification and acceptance gates still apply. Entirely new functions or substantial functionality prompt Claude to consult on scope unless the owner has already approved it.

Attempts are recorded before publication. An interrupted or failed delivery retains the candidate and requires reconciliation through the existing publication commands; it does not repeatedly create PRs on every tick. No worker transcript or private command is copied into an automatically generated PR description. Explicitly prepared candidates retain their existing review flow. Local checks passing does not imply remote GitHub CI has passed.

Capo's [writing guide](writing-style.md) documents the source analysis and the shorter style used for plans and results.

In the review thread, `@capo approve` or `@capo approve OBJECTIVE_ID` approves the most recent fully delivered review of that unchanged candidate. Capo looks up the exact approval code from its delivery record. A different thread, changed candidate, missing preview, or approval sent before that preview cannot use this shorthand. The explicit `approve OBJECTIVE_ID DIGEST` command remains available.

## Finish approved work

Set `merge_after_approval: true` in a private repository alias to make approval finish delivery: publish, wait for passing GitHub checks, mark the draft ready, squash-merge the exact approved commit, then delete its branch. The default is false. With routine automatic publication enabled, that standing permission also covers this finish step for newly delivered routine changes. Existing PRs are not enrolled retroactively; approve one explicitly to enroll it.

Capo checks at most once per minute while waiting. Missing or pending checks and GitHub branch rules delay merging. Failed checks, requested changes, conflicts, changed PR content, or uncertain writes stop automatic delivery for inspection. It does not use administrator overrides. Branch deletion uses an exact-commit lease and only the objective's own branch; new work pushed to that branch is preserved. The final Slack message confirms both merging and cleanup, or reports that delivery needs attention. Old local candidate checkouts remain as private evidence.

## Upgrade an existing Slack app for thread replies

In the Slack app settings, add `message.channels` under **Event Subscriptions → Subscribe to bot events** and add `channels:history` under **OAuth & Permissions → Bot Token Scopes**. For private channels, also add `message.groups` and `groups:history`. Save and reinstall the app to the workspace so the new scopes take effect. Keep the existing `app_mention` subscription. If Slack issues a different bot token, update only the private environment file and restart Capo.

The first mention is recorded durably, so clarification threads work even before an objective exists. Tagged replies that arrive through both event subscriptions are deduplicated. Owner/workspace/channel checks are repeated at intake and execution; bots, edits, unknown threads, and untagged top-level messages are ignored. Capo does not fetch channel history for this feature. Slack nonetheless grants channel-wide event visibility through its history scope; Capo filters events before retaining them. See Slack's [message event](https://docs.slack.dev/reference/events/message/) and [public-channel history scope](https://docs.slack.dev/reference/scopes/channels.history/) documentation.

Generated replies use a shared plain-text formatter before delivery is split into chunks. It removes Markdown heading/emphasis markers and stray trailing model protocol tags, while preserving fenced/inline code and URLs. Mentions remain escaped. Scheduled delivery retains the exact formatted text for crash reconciliation; formatting changes cannot justify blindly reposting a message.
