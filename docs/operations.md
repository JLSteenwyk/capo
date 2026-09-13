# Operating Capo

Capo is a local supervisor for trusted GitHub development projects. Claude Code plans and accepts work; configured Codex and Grok workers implement and review it. SPARKITscience is the default team name; the Slack bot is named `capo`. This runbook covers operating the first development workflow, not the broader [proposed architecture](architecture.md). Consult [validation](validation.md) and the [completion checklist](completion.md) for what has actually been verified.

## Prepare the agent computer

Use Python 3.11 or newer, Git, authenticated Claude Code and Codex CLIs, and authenticated GitHub CLI for issue intake or publication. Install Capo in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[slack]'
.venv/bin/capo --version
.venv/bin/capo doctor
```

`doctor` checks executable availability and versions; with a Lima transport selected, it also checks the guest and its isolation configuration, Grok binary, bubblewrap, and login-file presence. Presence alone does not prove a valid login, subscription capacity, or a working model response. Use each provider's normal subscription login; inspect its effective billing configuration. Capo removes ambient API-key variables but cannot establish the billing mode selected by every provider customization. Do not configure an API fallback when only subscriptions are authorized.

Set up Grok using [the Linux VM instructions](grok-linux.md). Keep the VM's native sandbox enabled. Its guest authentication belongs in the guest's private account, outside task staging and Git. Do not mount the host home directory or Docker socket, forward the host SSH agent, or copy unrelated credentials. Copy [the provider example](../integrations/grok/providers.example.json) into private configuration, set the VM name and guest binary, and select it before queuing objectives:

```sh
export CAPO_PROVIDERS_CONFIG="$HOME/.config/capo/providers.json"
capo doctor
```

Alternatively supply `--providers-config /private/path/to/providers.json` before the command. Capo captures this configuration in each new objective, including Slack intake, so changing the environment cannot silently reroute existing work. Local Grok remains the default without this selection. Confirm the configured transport's health before selecting Grok for objectives. A stopped VM or expired login is an integration failure, not permission to substitute a provider.

The transport stages only a prompt and schema in a private guest attempt directory. It retains a unique attempt identity and terminal receipt, removes staged task material after normal completion, and uses a deadline, heartbeat, connection closure, and cancellation marker to bound remote work. Guest provider session history may remain private in its home. An unreachable guest prevents recovery until its attempt can be reconciled; a host timeout alone does not establish remote termination.

Keep machine configuration outside the checkout. Runtime state defaults to `~/.local/share/capo`; set `CAPO_HOME` or supply `--home` before the subcommand to select another private directory. Keep configuration and runtime state restricted to the operating account. Use the same state directory for CLI and Slack so they see the same objectives.

For Slack, follow [the app setup instructions](slack.md), with actual owner/workspace/channel IDs only in the private configuration. Install the app, invite it into the configured channel, and supply bot/app tokens through a private local environment or credential manager. Never put tokens in chat, command arguments, committed examples, or publication text.

## Start and stop

Start Socket Mode in the foreground so startup errors are visible:

```sh
.venv/bin/capo slack-setup --config /private/path/to/slack-config.json
.venv/bin/capo slack --config /private/path/to/slack-config.json
```

The setup command resolves Slack identities; the service opens a live connection. The configured owner can submit objectives using the syntax in [Slack usage](slack.md). Repository aliases bind requests to preselected paths and verification commands. Do not launch another runner against the same state while its supervisor is active.

Keep the machine awake and connected during work. Ctrl-C or SIGTERM requests service shutdown and cancellation of its active child. A separate local watchdog observes supervisor death and terminates the worker process group; the guest has its own deadline and connection/heartbeat supervision. These mechanisms do not authorize blind retry after a hard crash. Inspect the recorded terminal state before restarting work. Stop an unused Grok VM only after its work has stopped:

```sh
limactl stop capo-grok
```

There is no bundled operating-system service installer. If using an external service manager, provide the same account, executable PATH, private configuration, and `CAPO_HOME`; use graceful termination and prevent overlapping instances. Validate crash recovery before unattended use.

## Run a development objective

The source repository must have a commit and a clean worktree. Install its test dependencies before queuing work. Choose verification commands that test the requested behavior; they execute under your operating account without a shell.

```sh
capo add "Fix the parser and cover the reported edge case" \
  --repo /absolute/path/to/project \
  --check 'python3 -m unittest discover -s tests -q' \
  --workers codex grok --reviewer auto \
  --max-calls 24 --max-rounds 3 --timeout 900
capo run OBJECTIVE_ID
capo show OBJECTIVE_ID
capo events OBJECTIVE_ID
```

Provider calls and failed attempts consume the objective's durable budget. The timeout applies per command, not to the entire objective. A `completed` result requires passing checks, independent approved reviews, and Claude's acceptance. It does not mean a PR has been published or merged.

For GitHub issue intake, replace `add` with:

```sh
capo issue ISSUE_NUMBER --github OWNER/REPO \
  --repo /absolute/path/to/project \
  --check 'python3 -m unittest discover -s tests -q' \
  --workers codex grok --reviewer auto
```

Issue intake reads and deduplicates the issue; it does not post comments. For an issue that requests changes to Capo itself, include `--self-improvement` to freeze the original regression suite; its trusted Capo checks are selected automatically. Explicitly selecting a smaller team is supported, but a two-provider success does not validate the three-provider deployment.

## Clarification and follow-up in Slack

Every command requires an @mention in the configured channel. Reply in the objective's original thread:

```text
@capo clarify: Preserve the existing function signature.
@capo followup: Also cover an empty input.
@capo status OBJECTIVE_ID
@capo cancel OBJECTIVE_ID
```

When Claude needs indispensable information to plan, the objective enters `awaiting_input` and the thread receives its question. An owner reply resumes planning in the same checkout. Waiting does not spend additional worker calls. Follow-ups are durable, deduplicated, and incorporated at safe execution boundaries. They retain existing candidate changes and cumulative budgets, invalidate unpublished acceptance/publication state, and cannot change trusted checks or policy. Once publication has started, use a new objective after integrating the published work.

## Review and deliver

Inspect the objective's workspace and private artifacts, including `changes.patch` and `delivery.md`. Keep prompts, raw provider output, test logs, and process records private. Review changes for sensitive values before preparing an external result.

```sh
capo prepare OBJECTIVE_ID --github OWNER/REPO --base main
# Inspect the generated patch, pull-request.md, and publication.json locally.
capo publish OBJECTIVE_ID --digest PREPARED_DIGEST
capo sync OBJECTIVE_ID
```

Preparation creates a local candidate commit and exact publication digest. Supply `--body-file /private/path/to/reviewed-pr.md` to use reviewed PR text verbatim. Before publication starts, repeat `prepare` with a new body file to revise the description while retaining the verified commit and branch. This generates a new digest and invalidates any Slack preview approval; inspect the revised artifacts and use the new digest. The title and target cannot change once prepared, and the body cannot change after publication starts. Repeating identical preparation is safe. Publication pushes the new objective branch and opens a draft PR. The target must match the source GitHub origin. Changed content, a conflicting branch, or movement of the remote base prevents publication. An uncertain publication should be retried against its existing record and exact digest so Capo can reconcile remote state; do not create another objective merely to repeat the write.

For Slack publication, the repository alias must privately enable `allow_publication` and set the intended `publication_base` and `github_auth`. In the objective thread:

```text
@capo prepare OBJECTIVE_ID
@capo approve OBJECTIVE_ID EXACT_DIGEST_FROM_PREVIEW
@capo sync OBJECTIVE_ID
```

Preparation sends the full diff and PR preview with the target and digest. Approval is available only after the full preview was delivered. It binds to that exact verified candidate; follow-ups or changed content require new verification and preparation. Oversized previews require CLI review and publication. Slack authorization is still checked for every action. No command merges a PR.

`sync` reads PR state, review status, and CI results. Verify the reported remote commit still matches the accepted candidate. Publication is not merging, and the service does not automatically repair failed CI. Use an explicitly authorized follow-up objective for additional work.

If an environment GitHub token overrides the intended saved login, deliberately select `capo --github-auth keyring ...`. Never print a token while diagnosing authentication.

## Recover interrupted work

Start with `capo list`, `capo show OBJECTIVE_ID`, and `capo events OBJECTIVE_ID`. Inspect private attempt records and current local/guest process state. A running status in SQLite is not proof that a process remains alive, and a temporary observation failure is not proof that it stopped.

| Observed condition | Next action |
| --- | --- |
| Worker is confirmed active | Observe that existing attempt or cancel it through its owning supervisor; do not start another. |
| Objective is `blocked` because of authentication or VM availability | Restore the selected integration, inspect the retained candidate, then use `capo run OBJECTIVE_ID --retry`. |
| Objective is `awaiting_input` | Answer the recorded question with an @mention in its original Slack thread, or cancel it. Re-running without new input does not resume planning. |
| Objective is `cancelled` | Inspect the last applied task and use explicit `--retry` only when continuation is intended. |
| Supervisor is gone but objective remains `running` | Reconcile local and remote attempts, then run `capo recover OBJECTIVE_ID`; if recovery succeeds, use `capo run OBJECTIVE_ID --retry`. |
| Recovery reports a possibly active process | Investigate its identity and process group; do not delete process metadata or bypass the guard. |
| Round or call budget is exhausted | Create a revised, bounded objective; retries do not reset consumed budgets. |
| PR publication outcome is uncertain | Inspect the publication record and remote PR/branch, then retry the same exact publication. |

Recovery does not automatically rerun uncertain attempts. Retain artifacts for diagnosis. Never clear locks, alter acceptance state, edit recorded digests, or disable sandboxing to turn an uncertain run into a success.

For backups, stop the service and reconcile active workers first. Back up the complete private state directory, including SQLite and its associated files, workspaces, baselines, and artifacts. Do not back up only the main SQLite file while it is being written. Restoring on another machine also requires path reconciliation and fresh provider authentication; copying state alone does not constitute supported host migration.

## Improve Capo

```sh
capo improve "Improve one specific recovery diagnostic" \
  --repo /absolute/path/to/capo --workers codex grok --reviewer auto
capo run OBJECTIVE_ID
capo prepare OBJECTIVE_ID --github OWNER/capo --base main
```

The candidate is separate from the running supervisor. Autonomous apply refuses edits to Capo's core enforcement and bootstrapping modules, including runtime, credentials/transport handling, store, publication gateway, entry points, and packaging. This applies to ordinary objectives targeting a Capo source tree too; using `add` does not bypass it. Such changes require owner review and maintenance outside autonomous apply. Choose an improvement in allowed functionality, documentation, or tests; do not remove the boundary to complete a task. Original tracked tests are frozen outside its checkout, hashed, and run against candidate code, followed by candidate tests. Review evidence that both suites passed and the baseline remained intact. Use the same exact-content publication flow. The running supervisor is not replaced automatically, and neither promotion nor rollback is autonomous. Frozen tests are regression safeguards, not containment against malicious code running as the operating account.

## Routine health and release checks

Check provider availability, objective states, disk space for private artifacts, Slack connection health, and published PR/CI state. Exercise model authentication separately from executable discovery. Remaining subscription quota may be unknown; local call counts are not an estimate of it.

Before shipping runtime or integration changes:

```sh
python3 -m unittest discover -s tests -q
git diff --check
```

Automated tests must not contact live providers or send Slack/GitHub messages. Live checks need separate evidence. Update [validation](validation.md) with sanitized outcomes and [completion](completion.md) with remaining gaps. Inspect staged files for credentials, actual Slack identities, private paths, and generated artifacts before committing and pushing.

Desktop automation, general recurring schedules, learned routing, unrestricted workers, and migration to a dedicated computer remain future capabilities. See [computer-use boundaries](computer-use.md). The current deployment assumes trusted repositories and verification commands.

## Enable GitHub CI

The installed workflow at `.github/workflows/tests.yml` runs the credential-free unit suite on Python 3.11 and 3.14 with read-only repository permissions. A reusable copy is provided at `integrations/github/tests.yml`. Both jobs passed on the first mainline run.

Installing or updating a workflow requires an appropriately authorized GitHub login. If GitHub rejects a workflow push for missing scope, run `gh auth refresh --hostname github.com --scopes workflow` locally and complete the browser flow. Do not paste credentials into chat. An empty CI status is not a passing CI run; inspect the checks on the exact commit being reviewed.
