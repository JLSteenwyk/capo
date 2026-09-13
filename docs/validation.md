# Validation record

September 12, 2026, macOS, Python 3.14.5.

## Automated checks

`python3 -m unittest discover -s tests -q` passes 101 tests. Coverage includes the full objective loop with fake providers, replayable Git patches, review-driven revision, verification/acceptance gates, bounded model calls, interrupted-run recovery, exclusive supervisor ownership, path protections, provider errors, subprocess termination, log-size limits, explicit team selection, GitHub publication reconciliation, frozen self-improvement regressions, and owner-only Slack intake.

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

GitHub publication tests use real local Git commits and a fake GitHub gateway; a real local bare-remote test also verifies that publication cannot overwrite an existing objective branch. No test PR was posted to GitHub. Code milestones were committed and pushed to the requested repository separately.

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

GitHub rejected CI workflow publication because the saved OAuth login lacks the workflow scope. The reviewed workflow is retained at `integrations/github/tests.yml`; live CI remains unverified until installation. Slack bot/app credentials remain absent, so live Slack verification is still pending.
