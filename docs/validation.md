# Validation record

Current audit: September 13, 2026. Historical entries below retain their original scope and limitations.

## Current release evidence

The required local suite passes 167 tests. [Capo mainline CI](https://github.com/JLSteenwyk/capo/actions/runs/34763624842) passed on Python 3.11 and 3.14. Routine draft delivery now has automated coverage for policy opt-in, accepted-tree verification, independent acceptance, new-function escalation, failed delivery, repeat prevention, and completion-message ordering.

The owner submitted PhyKIT issue #110 through live Slack. A bounded continuation passed its 326 targeted tests, Grok review, and Claude acceptance. With the owner's routine-publication policy enabled, the Slack service automatically published [PhyKIT draft PR #111](https://github.com/JLSteenwyk/PhyKIT/pull/111) and delivered its completion message to the original thread. Capo synchronized all seven successful PR checks: Python 3.10–3.13 tests, Linux and Windows wheel smoke tests, and documentation. The remote head matches the accepted commit; the owner subsequently merged the PR. Capo did not perform the merge.

The earlier Capo self-improvement remains a separate [draft PR #2](https://github.com/JLSteenwyk/capo/pull/2). A fresh audit found its frozen baseline intact, accepted tree unchanged, both recorded suites passing, Grok approval, and Claude acceptance after two rounds. Its exact head, `9170f824c53876e2ec15f967d5f69d3031f745b3`, passed the manually dispatched [Python 3.11/3.14 CI run](https://github.com/JLSteenwyk/capo/actions/runs/34764290111). Each job verified the checked-out SHA. This is candidate-specific CI evidence, not an attached PR status check; the older PR predates the workflow.

Live Slack status now returned a threaded reply to the owner. A documentation clarification test is in progress. Initial intake correctly refused the dirty maintenance checkout; after committing those changes, an operator reprocessed the same authenticated owner event and preserved its deduplication identity. Claude asked which page and what change the owner wanted. The owner's live follow-up was stored against the same objective and included in the next Claude prompt. Cancellation arrived while that second planning call was still running; the request became cancelled, its threaded cancellation reply was delivered, the candidate had no file changes, and no Claude worker remained. The second clarification answer was not observed, so this does not establish that Claude would have asked the requested section question. Manual approval remains to be verified.

That live cancellation exposed a macOS `PermissionError` during the watchdog's process-group probe. Cleanup now attempts kill and reap even after a denied probe, bounds its wait, and returns an explicit failure when group cleanup cannot be confirmed. The caller distinguishes that failure from confirmed cancellation. Five regression tests cover denied probes, denied signals, cancellation with uncertain cleanup, and confirmation after the group disappears or its final child is reaped. Routine delivery is verified under the owner's newer standing authorization; the separate manual approval interface remains covered by automated tests.


A controlled idle Slack service restart preserved all objective states, call counts, and notification checkpoints. An immediate overlapping start was refused by the service lock. After the old process was confirmed absent, the replacement started successfully. This verifies restart preservation on the real installation; synthetic tests separately cover active-run and message-delivery fault paths.

The final recovery audit found that cleanup uncertainty could previously become an ordinary failed check or be bypassed by retrying after the watchdog exited. Startup now records and fsyncs both supervisor and worker identities before allowing execution. A dedicated cleanup-uncertainty error aborts verification, and retry/recovery probes the recorded worker group without signaling recovered IDs. Other objectives cannot bypass an unconfirmed current-format attempt. Regression tests cover these cases, interrupted legacy attempts, and reuse of attempt directories without trusting stale receipts.

The owner later requested a preview of the already-merged PhyKIT PR using a bold-formatted Slack command. This exposed command normalization and stale publication-state handling. The adapter now recognizes the enclosing formatting directly and checks current GitHub state before preview or approval. Merged or closed PRs receive an informational reply without preparation or publication. Automated tests verify that neither write path is invoked. After deployment, an operator reprocessed the original authenticated owner event through the updated dispatcher; the real GitHub read returned MERGED and Capo delivered the confirmation to the original Slack thread. This was an operator-assisted replay, not a new owner approval or a new PR.

## Historical automated checks

`python3 -m unittest discover -s tests -q` passes 126 tests. Coverage includes the full objective loop with fake providers, replayable Git patches, review-driven revision, verification/acceptance gates, bounded model calls, interrupted-run recovery, exclusive supervisor ownership, path protections, provider errors, subprocess termination, log-size limits, explicit team selection, GitHub publication reconciliation, frozen self-improvement regressions, and owner-only Slack intake.

`git diff --check` passes. The CLI help and executable doctor run successfully.

## Live provider probes

| Provider | Version | Result |
| --- | --- | --- |
| Claude Code | 2.1.263 | Structured JSON response validated |
| Codex | 0.154.0 | Structured JSON response validated |
| Grok Build | 1.0.13 | Sandbox initialization failed before a model response |
| Grok Build, isolated downloaded copy | 1.0.30 | Same sandbox initialization failure |

Grok reported that the runtime-socket deny path `/var/run/docker.sock` is a symlink and refused to start without its requested read-only protections. The original installation and sandbox settings were preserved. A live Grok response contract remains unverified in the native macOS process. The Linux VM route below succeeded.

## Live development objective

A temporary Git repository contained a faulty `add(a, b)` function that subtracted its arguments. Capo ran with explicit `--workers codex --reviewer claude` configuration:

1. Claude produced a plan.
2. Codex proposed the corrected file.
3. The runtime applied it in a separate clone.
4. A Python verification command passed positive, mixed-sign, and zero cases.
5. A separate Claude review approved the change.
6. Claude accepted the objective against the recorded evidence.

The objective completed in four model calls and one round. The original checkout remained unchanged. The runtime retained the staged fix, patch, attempt artifacts, verification output, and delivery report.

This verifies the small local workflow. It does not establish general coding quality, hostile-code isolation, unattended reliability, remaining subscription capacity, Grok interoperability, or future computer-use capabilities.

## Live self-improvement

Capo ran an improvement against its own committed source: add `capo --version` using a shared version constant. Claude planned; Codex supplied a two-file patch; the frozen original tests and candidate tests both passed (41 tests in each suite at that revision); Claude reviewed and accepted. The workflow completed in four model calls. The verified patch was inspected and integrated into this repository, and `python3 -m capo --version` prints `capo 0.1.0`.

## Slack adapter

The optional package installed successfully with Slack Bolt 1.30.0 and Slack SDK 3.44.1. Socket Mode connect/close interfaces were checked against the installed SDK. Tests use a fake Slack client and cover workspace/channel/owner restrictions, duplicate event delivery, restart deduplication, thread replies, queued cancellation, repository allowlisting, and token removal from child processes. No Slack messages were sent and no live workspace connection was established. The owner and channel name are saved privately. Bot/app tokens and resolved workspace/channel IDs are required for live validation.

GitHub publication tests use real local Git commits and a fake GitHub gateway; a real local bare-remote test also verifies that publication cannot overwrite an existing objective branch. At this earlier validation stage, no test PR was posted to GitHub. Code milestones were committed and pushed to the requested repository separately.

## Live Grok Linux VM probe

Grok Build 1.0.30 ran successfully with `--sandbox read-only` in Debian 13 ARM64 (Linux 6.12.95) under Lima 2.2.0, after installing bubblewrap 0.12.0. The existing CLI login authenticated with no API key supplied. No host folders or Docker socket were mounted.

A structured JSON smoke test passed. Capo's provider adapter then ran inside the guest: Grok proposed an arithmetic correction, three arithmetic checks passed, and a separate Grok call approved the correction. Both responses passed Capo's schemas. The live response exposed a camelCase `structuredOutput` field; the adapter and regression tests now cover it, text fallback, errors, and incomplete responses.

The copied authentication file was removed and the test VM stopped afterward. This initial probe preceded the integrated host-to-VM transport described below. See [the Linux setup and limitations](grok-linux.md).

## Integrated transport and control-plane implementation

The host-side Lima transport returned a valid live Grok review through `Providers.call`, using the configured Linux guest and its existing CLI login. Automatic transport selection is now stored with each queued objective. Unit tests cover EOF cancellation, deadline termination, private staging cleanup, configuration checks, missing authentication, and refusal to recover an uncertain guest.

Local subprocesses now run beneath a parent-death watchdog. A test kills the owning supervisor with SIGKILL and verifies that the worker exits. Follow-up and publication tests cover durable owner input, preserved candidate work, stale approval rejection, complete Slack preview delivery before approval, and publication recovery. Live Slack remains unverified because bot/app credentials are absent.

## Recovery and delivery hardening

The required unit suite now contains 101 tests. Added coverage includes durable cancellation across Slack restarts, consumed cancellation replay, paused clarification, resumable/rate-limited Slack previews, task-file prioritization in bounded snapshots, package-shadow protection for enforcement modules, guest reboot receipts, and compressed SSH bootstrap execution.

A live synthetic fault test killed the host transport supervisor with SIGKILL. The Linux guest stopped its worker and recorded `Host disconnected`; recovery reconciled the terminal receipt. A repeated timeout stress test exposed an overlapping-interruption race that could leave a running receipt after cleanup. Cleanup now normalizes terminal state, with a deterministic regression test and repeated timeout probes.

The first integrated issue/self-improvement run used Claude, Codex, and Grok. Independent review rejected missing tests/docs and a SQLite WAL concern. Concrete follow-up context led to a revised implementation, but the run remained blocked on a generated test-fixture error and a transport failure; no PR was published from that state. The transport failure was an OpenSSH multiplexed command-size error, addressed with a smaller compressed guest bootstrap. The candidate is retained for a bounded continuation against the corrected platform.

At that stage, GitHub rejected CI workflow publication because the saved OAuth login lacked the workflow scope. The reviewed workflow is retained at `integrations/github/tests.yml`; live CI remains unverified until installation. Slack bot/app credentials remain absent, so live Slack verification is still pending.


## Integrated development and self-improvement continuation

A bounded continuation of the real objective-report issue completed against the corrected platform. The prior blocked attempt and its candidate were preserved; the new candidate started from those three feature files with a fresh, stronger frozen baseline. Claude Code planned the work, Codex corrected and expanded the implementation/tests, and Grok Build reviewed through the configured isolated Linux transport. An initial review rejection for missing schema context prevented acceptance; operator follow-ups supplied the actual Store schema. The final review approved, both checks passed, and Claude accepted the full feature in ten provider calls without changing enforcement code.

The frozen suite passed 101 tests and the candidate suite passed 116 tests. A live `capo.report` invocation against the completed objective returned its status and verification summary while preserving source SQLite contents and modification times. This is an assisted integration demonstration, not a claim of fully unattended development quality. The final planner summary was a placeholder, so the publication description was prepared separately for explicit review rather than treating model-generated prose as sufficient delivery evidence.


The verified candidate was published through Capo's digest-bound CLI flow as [draft PR #2](https://github.com/JLSteenwyk/capo/pull/2). Remote reconciliation confirmed that its head matches the accepted commit and its body matches the reviewed description. PR synchronization reports an open draft with no CI checks; an empty status is not a passing CI run. The PR has not been merged.

The mainline suite now passes 107 tests, including restart discovery of undelivered Slack clarification/terminal notifications, rate-limited chunk recovery, and publication-description revisions that invalidate previous approval digests. Live Slack remains blocked on local credentials and workspace resolution; CI installation was still blocked at that stage; the authorization is now resolved as recorded below.


## Live GitHub CI

After the owner added workflow authorization, commit `dfb14d3` installed `.github/workflows/tests.yml`. Both Python 3.11 and 3.14 jobs passed in [the first mainline run](https://github.com/JLSteenwyk/capo/actions/runs/34760445246). This verifies mainline CI installation and execution. Draft PR #2 predates the workflow and still requires checks against its own code; mainline success is not evidence for that PR head. Slack setup remains a separate live-verification requirement.


## Live Slack intake and conversational interface

The owner’s app mention reached Capo over Socket Mode and received a threaded help response. A PhyKIT objective also reached planning, clarification, implementation, tests, review, and acceptance evaluation through the live Slack service. The implementation passed its targeted 326-test suite, but a circular plan criterion incorrectly demanded PR publication before candidate acceptance. The prompts now separate code acceptance from the subsequent approval-gated publication step; that failed attempt remains retained rather than marked complete.

A live Claude interpretation of “Can you check if PhyKIT has any issues that need to be addressed?” selected read-only issue listing for the configured PhyKIT alias. The 126-test automated suite covers bounded asynchronous interpretation, durable replay, owner/repository restrictions, conversational follow-ups and status, explicit-only publication approval, and a single persisted plan notification instead of per-stage chatter. Live conversational delivery and PhyKIT publication remain to be exercised after the service update.

## Concise Slack review and short approval

The owner requested plain-language messages and a shorter approval step. Prepare now sends a short description and destination; full code is available on request. Approval without a copied code resolves only from a completed owner review in that same thread, for the unchanged candidate. Wrong-thread, changed-content, and approval-before-preview cases are refused.

A live documentation self-improvement passed 167 frozen tests and 167 candidate tests, Grok review, and Claude acceptance. The owner received the review and sent a short approval. The previous handler rejected the missing code. After the fix, an operator replayed the original authenticated event through the updated handler: [draft PR #3](https://github.com/JLSteenwyk/capo/pull/3) was created, and Capo synchronized an exact accepted-commit match. The retry was operator-assisted. No PR was merged.

The updated mainline suite passed all 171 tests; the final additional Slack assertions passed all 46 Slack tests. The bounded source-context fix is also integrated. These local results do not stand in for hosted CI on a particular commit.
