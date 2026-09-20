# Controlled self-updates

Capo's external macOS supervisor watches one configured GitHub repository and branch every five minutes. After a self-improvement PR merges, it can deploy that revision without a separate manual restart. Existing implementation, review, publication and merge permissions still apply. The updater does not approve or merge PRs.

The supervisor runs from a pinned release independently of the Slack bot. It:

1. Fetches the configured branch into a private clone and identifies an exact commit.
2. Requires all configured GitHub Actions checks to succeed on that commit; missing, pending, stale and failed checks defer deployment.
3. Creates a separate release checkout and runs the full regression suite without service tokens in the test environment. The developer checkout is untouched.
4. Rechecks the branch and CI. It asks the running service to drain and waits for an idle acknowledgement from its current process. Active requests, objectives, browser sessions and scheduled work defer the switch. New Slack arrivals remain durably queued during the handoff.
5. Saves the prior LaunchAgent configuration, switches the service to the candidate module, and holds queued work during probation. A fresh receipt must match the managed process, revision and handoff, and Slack must stay connected for ten seconds.
6. Promotes the healthy release and resumes work. If startup or health checks fail, it restores the previous launch configuration. An interrupted deployment is recovered by the independent supervisor on its next invocation. A failed revision is not retried automatically; a new commit is needed.

The updater only changes code and launch configuration. Configuration, credentials, schedules, memories and queued work remain in their existing private locations. Rollback restores code; it does not restore databases or undo external actions. Dependency, updater and core storage-module changes require a manual rollout. Other releases must remain compatible with existing persistent data; passing regression tests is not proof that an arbitrary migration is reversible. The startup probe verifies import/startup and Slack connectivity, not every integration or future execution path.

## Private setup

Create a private JSON configuration outside the repository, for example `~/.config/capo/updater.json`:

```json
{
  "repository": "example/capo",
  "branch": "main",
  "home": "/absolute/private/capo-state",
  "service_plist": "/absolute/home/Library/LaunchAgents/org.capo.slack.plist",
  "python": "/absolute/capo-environment/bin/python",
  "initial_revision": "0000000000000000000000000000000000000000",
  "required_checks": ["unittest (3.11)", "unittest (3.14)"]
}
```

Replace the initial revision with the verified current commit. The shared Python environment must already contain the installed optional integrations; releases do not reinstall dependencies. The service launcher must use the `capo` console script or `python -m capo`. Bootstrap once using a manually verified restart so the running service supports the drain/health handshake.

Run from the verified updater checkout:

```bash
python -m capo.updater --config ~/.config/capo/updater.json
python -m capo.updater --config ~/.config/capo/updater.json --status
```

After validating the initial deployment, install the five-minute launchd job from the pinned initial release:

```bash
python -m capo.updater --config ~/.config/capo/updater.json \
  --install-watchdog /absolute/private/capo-state/updates/releases/INITIAL_COMMIT
```

The supervisor itself stays pinned; it cannot autonomously replace its own deployment policy. A future updater upgrade requires a reviewed manual installation. GitHub reads use the existing `gh` keyring login, with token environment overrides removed. The LaunchAgent and private logs use owner-only permissions. Never commit this machine's configuration or logs.

`updates.status` exposes a bounded deployment status to Capo. Detailed state, test logs, prior launch configuration and release checkouts live under `CAPO_HOME/updates`. They may contain private launch settings and must remain private. No releases are deleted automatically, including the supervisor's pinned release and rollback target.

To pause automatic updates, unload `org.capo.updater` with launchctl. If status is `rollback_failed`, inspect the saved launch configuration and service logs locally before resuming; the supervisor stops deploying until the problem is resolved. Do not clear a probation marker while its candidate is unverified. `--redeploy` revalidates the current branch without bypassing checks or the failed-revision hold.


## Automatic merge before deployment

Per-repository `allow_publication`, `auto_publish_routine`, and
`merge_after_approval` together authorize routine accepted changes to publish and
merge without another owner command. Despite its name, `merge_after_approval`
also covers standing authorization granted by `auto_publish_routine`. Capo waits
for GitHub checks, matches the accepted commit, and removes the work branch only
if it still points to that commit. The updater then handles deployment separately.

This applies to bounded existing-behavior fixes and documentation, including
eligible self-improvements when `allow_self_improvement` is enabled. Larger changes,
new source interfaces, and protected enforcement changes remain outside routine
eligibility. `development.policy` reports effective permissions per repository
and the last deployment-supervisor status without exposing private configuration.
A question about capabilities does not itself queue development work.

### Broader feature authority

A repository may opt into `automatic_change_scope: "features"` alongside
`allow_self_improvement`, `allow_publication`, `auto_publish_routine`, and
`merge_after_approval`. The existing flag name `auto_publish_routine` enables the
publication pipeline; `automatic_change_scope` chooses its eligibility policy.
Without the new setting, the original conservative routine policy remains.

Feature scope allows new Python modules, functions, classes, interfaces and
imports, with no three-file or changed-line eligibility cap. Markdown and tests
are also supported. This does not increase execution budgets or guarantee an
arbitrarily large task will finish in one objective. Source snapshots and the
existing implementation limits still apply.

Protected enforcement modules, sensitive configuration, dependencies, recognized
migration paths, deleted files, executable/nonregular files and other file formats
still require review. Independent reviewers must flag irreversible migrations
wherever implemented; path matching alone cannot establish migration safety.
Frozen baseline tests, candidate tests, independent review, final acceptance,
exact-tree publication, GitHub checks, merge reconciliation and deployment health
checks remain mandatory. The updater's manual-rollout boundaries are unchanged.

Development instructions explicitly treat repository content and PR metadata as
public. Private conversations, mail/calendar records, personal preferences, local
configuration and runtime artifacts belong outside Git; examples must be synthetic.
PR preparation and publication both check recognizable secret/identity markers,
local home paths and private artifact paths. Automatic PR text does not copy the
owner's private request or agent summaries. Pattern checks supplement independent
privacy review; they cannot recognize every sensitive fact in free-form prose.
