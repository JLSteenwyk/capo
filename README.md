# Capo

Capo is the platform. **SPARKITscience** is the default team name for this installation. Set `CAPO_TEAM_NAME` or pass `--team-name "Your team"` before a command to customize the name used in objectives, agent context, delivery reports, and PR descriptions.

Slack is the primary human interface. The included [Slack adapter](docs/slack.md) accepts owner-only objectives, provides a short plan and final result, and supports status, cancellation, thread follow-ups, and exact-candidate publication approval. The SPARKITscience workspace connection has been verified live. Natural-language messages can list repository issues, request work, check progress, and ask for a preview; small verified changes can publish draft PRs under an explicitly configured routine policy; broader changes use exact-candidate approval.

Claude Code leads development objectives, delegates implementation to Codex and Grok Build, evaluates reviews, and decides whether the result meets the objective. A local Python runtime owns the queue, checkpoints, process supervision, verification, and artifacts.

This is an early working vertical slice for small changes in **trusted local repositories**. The broader autonomous organization is described in [the architecture](docs/architecture.md). [Dedicated-computer and computer-use plans](docs/computer-use.md) are part of that design.

## Run

Requires Python 3.11+ on macOS or Linux, Git, and authenticated `claude`, `codex`, and `grok` CLIs. GitHub issue intake also uses authenticated `gh`. No Python runtime dependencies are required.

```bash
python3 -m capo doctor

python3 -m capo add "Fix the CSV parser's handling of quoted commas" \
  --repo /absolute/path/to/your/repo \
  --check 'python3 -m unittest discover -s tests'

# Use the objective ID returned by add:
python3 -m capo run OBJECTIVE_ID
python3 -m capo show OBJECTIVE_ID
python3 -m capo events OBJECTIVE_ID
python3 -m capo list
```

Run these commands from this checkout, or install the CLI with `python3 -m pip install -e .` and use `capo`. The source repository must have a commit and a clean working tree. Verification commands are explicitly supplied by you and run as argument arrays, without a shell. They must be appropriate for the target project, with dependencies already available.

State defaults to `~/.local/share/capo`. Set `CAPO_HOME` or pass `--home /path/to/state` **before** the subcommand to use a different directory. Moving to the agents' dedicated computer requires copying persistent state with path migration and authenticating the providers there; the Grok Lima transport is supported, but there is no general remote host controller yet.

### GitHub issue intake

```bash
python3 -m capo issue 42 --github OWNER/REPO \
  --repo /absolute/path/to/checkout \
  --check 'npm test'
```

This reads the issue and queues a local objective. Repeated intake of the same issue for the same checkout returns the existing objective. It does not publish comments or change the issue.

### Deliver a draft pull request

After an objective completes, prepare the exact commit and PR text locally:

```bash
python3 -m capo prepare OBJECTIVE_ID --github OWNER/REPO --base main

# Inspect changes.patch, pull-request.md, and publication.json in its artifacts directory.
# Use the digest printed by prepare to publish that exact candidate:
python3 -m capo publish OBJECTIVE_ID --digest PREPARED_DIGEST
python3 -m capo sync OBJECTIVE_ID
```

`prepare` creates a commit in the isolated clone and performs no remote writes. Supply `--body-file /private/path/to/reviewed-pr.md` to use reviewed PR text verbatim. Before publication starts, repeat `prepare` with a new body file to revise the description while retaining the verified commit and branch. This generates a new digest and invalidates any Slack preview approval; inspect the revised artifacts and use the new digest. The title and target cannot change once prepared, and the body cannot change after publication starts. Repeating identical preparation is safe. `publish` pushes only its objective branch and creates a draft PR. The target must match the source repository's GitHub origin. Changed code, a moved remote base, a conflicting branch, or a mismatched digest prevents publication. Retries reconcile existing remote work, including a PR created before a connection failure. `sync` records PR state, review status, and CI results, and reports whether the remote commit still matches the verified candidate. It does not merge or close anything.

GitHub commands normally honor `GH_TOKEN`/`GITHUB_TOKEN` and the normal `gh` configuration. To deliberately use a saved keyring login when an environment token is invalid, put `--github-auth keyring` before the subcommand, for example `python3 -m capo --github-auth keyring publish OBJECTIVE_ID --digest PREPARED_DIGEST`. Credentials are never copied into Capo state.

## What a run does

1. Creates an independent local clone at the recorded source commit and removes its `origin` remote.
2. Gives Claude a bounded source snapshot and asks for a plan of 1–6 sequential tasks with acceptance criteria.
3. Calls the selected worker for structured file changes, validates paths, and applies those changes locally.
4. Runs your verification commands and saves their outputs.
5. Obtains review from the other provider for each implementation provider used.
6. Asks Claude to accept or request revision. Completion requires passing checks, approved reviews, and Claude's acceptance together.
7. Writes a delivery report and a replayable patch. The clone retains staged changes for inspection and commit.

Each rejected round feeds the evidence into another implementation pass, up to the configured limit. The default limits are three rounds, 24 provider calls, and 900 seconds per command. Set `--max-rounds`, `--max-calls`, and `--timeout` when adding an objective.

To select a smaller available team explicitly, add `--workers codex --reviewer claude`. Claude still plans and makes the final acceptance decision, with a separate Claude session reviewing Codex's changes. The runtime rejects plans that use an excluded worker or assign implementation to the explicitly selected reviewer. No provider is silently substituted.

Artifacts are in `$CAPO_HOME/artifacts/OBJECTIVE_ID/`: prompts, provider output, structured results, process metadata, test logs, `changes.patch`, and `delivery.md`. Treat them as private project data.

### Interruptions and recovery

`Ctrl-C` or `SIGTERM` stops the current process group and records cancellation. After inspecting the artifacts, `run OBJECTIVE_ID --retry` resumes from the last applied task. A blocked provider attempt can also be retried this way; consumed calls remain counted. Exhausted round/call limits require a new objective with a revised scope.

A hard supervisor crash leaves an uncertain running state. Inspect the workspace and process records, then run `recover OBJECTIVE_ID` followed by `run OBJECTIVE_ID --retry`. Recovery refuses when a recorded process may still be alive. Capo never automatically reruns an uncertain attempt. One supervisor per state directory is enforced with a process lock. A watchdog stops local workers if their supervisor dies, and the VM transport uses an independent heartbeat lease. Slack cancellation requests survive service restarts.

## Improve Capo itself

```bash
python3 -m capo improve "Improve recovery diagnostics" \
  --repo /absolute/path/to/capo --workers codex --reviewer claude
python3 -m capo run OBJECTIVE_ID
python3 -m capo prepare OBJECTIVE_ID --github OWNER/capo --base main
```

The improvement runs in an isolated candidate clone. Capo preserves the original tracked tests outside that clone and runs them against the candidate, followed by the candidate's own tests. Changing or removing candidate tests does not remove the original regression checks. Baseline hashes are checked before and after verification. The running supervisor is not replaced; use the normal preview/publication workflow to review and ship a candidate. Frozen tests detect regressions and ordinary test weakening; they are not a security boundary against malicious Python with your OS account's privileges.

## Current boundaries

- **Subscriptions:** CLI authentication is reused. Ambient API-key variables are removed from child environments, but provider configuration or credential helpers can still select another billing mode; verify your setup. Quota is unknown, not estimated from local call counts. The runtime does not buy credits or silently switch providers on failure.
- **Implementation:** workers propose full text replacements. This first version uses sequential tasks and bounded snapshots that prioritize named task files; it does not yet offer general repository exploration, large/binary changes, parallel task graphs, or learned routing.
- **Permissions:** Claude and Grok are invoked with built-in tools disabled; Codex uses its read-only sandbox. File proposals cannot modify protected agent/Git/GitHub configuration, common secret files, or symlink paths. A separate clone prevents edit collisions. These controls are **not a complete sandbox**: CLIs can load host customizations, read-only does not imply no network, and trusted verification commands run with your OS account's privileges. Do not run this on untrusted repositories or assume that account credentials are isolated.
- **Context:** obvious secret paths are omitted, but there is no general secret scanner. Source snapshots are sent to the chosen providers. A task cannot modify files explicitly omitted from its snapshot.
- **GitHub delivery:** issue intake, local PR preparation, explicit draft publication, retry reconciliation, and PR/CI status reads work. Slack supports opt-in routine draft publication. Automatic issue polling, CI-triggered repair, and merging are not implemented. The original repository is kept unchanged.
- **Persistence:** objectives, checkpoints, events, Slack intake, and artifacts persist. The Slack service executes its queued objectives serially. There is no general scheduled-job engine, automatic rate-limit recovery, OS service installer, remote host controller, or desktop automation yet.
- **Learning:** explicit self-improvement objectives with frozen regressions are implemented. Automatic reflection, skill creation, learned routing, unattended promotion, and rollback management remain planned.

## Verify

```bash
python3 -m unittest discover -s tests -v
```

Tests use temporary repositories and fake providers. They exercise delivery, patch replay, revision, hard acceptance gates, budgets, recovery, locks, path validation, environment filtering, and timeouts without consuming model quota.

Publication tests additionally exercise GitHub retry reconciliation, exact-content digests, remote branch conflicts, base movement, and commit identity checks. GitHub operations use the supported [PR creation](https://cli.github.com/manual/gh_pr_create) and [PR listing](https://cli.github.com/manual/gh_pr_list) CLI interfaces.

Provider interfaces were checked against installed CLI help and official documentation: [Claude](https://code.claude.com/docs/en/headless), [Codex](https://developers.openai.com/codex/noninteractive), and [Grok Build](https://docs.x.ai/build/cli/headless-scripting). See the architecture for the staged roadmap and the distinction between current behavior and intended capabilities.

### Local integration status (September 12, 2026)

Live structured-response probes passed with Claude Code 2.1.263 and Codex 0.154.0. Grok 1.0.13 and an isolated copy of stable 1.0.30 both refused to initialize the read-only sandbox on this Mac because `/var/run/docker.sock` is a symlink. The installed Grok executable was preserved. Capo keeps the sandbox enabled. A subsequent [Linux VM test](docs/grok-linux.md) passed using Grok 1.0.30, the existing CLI login, and the read-only sandbox: implementation, three arithmetic checks, and a separate review all succeeded. An explicit [Lima transport](docs/grok-linux.md) now routes Grok requests into the VM with isolated staging, heartbeat cancellation, deadlines, and recovery receipts. The explicit Claude/Codex team above can be used meanwhile. This is a host integration failure, not evidence of a subscription quota problem.

See the [operations runbook](docs/operations.md) and [release evidence checklist](docs/completion.md) for setup, recovery, and outstanding live checks.
