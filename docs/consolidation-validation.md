# Consolidation validation record

The fixed-harness comparison uses pre-consolidation revision `d49c0e2`
and the consolidated implementation, including shared role memory, notification
controls, source links and configured worker routing. Artifacts, implementation hashes and
machine timings are stored privately under `CAPO_HOME/migration-validation`.
No live provider or personal-service call is part of this comparison.

The same 50 tests ran three times against each implementation, in separate Python
processes using the same test sources. Coverage includes Slack outage recovery,
attachment context, a screenshot-to-three-events journey, research and date
resolution, calendar changes and uncertain writes, multi-item completion, truthful
partial outcomes, and fresh source checks for tasks associated with email replies.
Assertions inspect durable state, preserved fields, output content and duplicate
writes rather than trusting a provider's completion message.

| Implementation | Tests per sample | Samples | Failures/errors | Recorded reasoning attempts per sample | Median harness duration |
| --- | ---: | ---: | ---: | --- | ---: |
| Before consolidation | 50 | 3 | 0 | 53 Claude | 0.479 seconds |
| Consolidated | 50 | 3 | 0 | 53 Codex | 0.490 seconds |

These are host regression checks with scripted provider decisions. They support
preservation of the tested outcomes, not an improvement in model judgment or
production response speed. Harness durations include local initialization and
synthetic adapters; they exclude real model and service latency. Provider counts include new durable recovery attempt records, not standalone
reasoning calls or real subscription consumption. Token usage, live provider
latency, and autonomous clarification quality were not measured. Imported
implementation paths were checked to prevent the baseline from loading current
code accidentally.
Scripted paths can verify that research and action composition are available and
that a chosen path avoids needless confirmation; they cannot establish how often
a real model chooses that path. The small timing sample supports no latency claim.

The fixed modules are `test_slack_recovery`, `test_assistant_journeys`,
`test_resolution`, `test_calendar_actions`, `test_work_inventory`,
`test_request_outcomes`, and `test_task_evidence`. New capability tests separately
cover provider selection, bounded private Codex images, practical-default receipts,
shared specialist memory, the unified overview, quiet periods and finding delivery
across overlapping automations. The full acceptance suite passes 915 tests; deployment and fresh Slack health
are verified separately through the existing supervisor.


## Acceptance evidence map

| Requirement | Implementation and inspected verification |
| --- | --- |
| Codex for new conversations, planning, coding, research and scheduled work | `reasoning_policy`, `capabilities`, `runtime`, `scheduled_requests`, `digest`, `heartbeat`; policy, CLI/objective and team-assignment tests; fixed-harness provider attempt counts above |
| Explicit and historical provider choices remain pinned | `selected`, `request_recovery`, objective `orchestrator`; policy and checkpoint tests authenticate saved inputs and preserve old provider metadata |
| One configured outage/capacity fallback, without routine headroom switching | Shared durable recovery and standalone `direct_call`; policy tests cover outages, image preservation, exhausted time budgets and failures that must not switch; worker tests distinguish capacity fallback from login/unclassified failure |
| Codex image support before removing dependencies | Private staging, format/size validation, CLI image flags, cleanup and read-only sandbox in `providers`; Codex image tests and synthetic image probe; official CLI reference and installed help confirm image flags |
| Remaining provider-specific dependencies are explicit | Capability guide identifies Claude WebSearch, browser action selection, legacy calendar/checkpoints and independent development review; optional X Search remains a separately metered Grok API |
| Single assistant and reusable coding, finance and research roles | `roles`, shared registry, `specialist_tools`; shared-conversation and team tests exercise role reads without another orchestrator |
| Isolated delegation preserves provenance and verified completion | `worker_delegation` intersects inherited read tools, bounds work, records source receipts and requires chief review; worker and request-outcome tests reject treating handoff as external action completion |
| Retire redundant new-request routing and preserve useful automation/history | New ordinary requests use the shared loop; authenticated legacy contexts remain resumable. Team tests preserve notebook bytes, share new memory and isolate owners; automation-day and schedule tests retain disabled-role and existing-schedule semantics |
| Store, inspect, correct and delete practical defaults privately | `practical_defaults` reuses `PersonalMemory` with exact owner quotes, revisions, provenance and receipts; default tests cover restart, correction, deletion, stale edits and owner isolation |
| Apply defaults, preserve current overrides and ask only for material ambiguity | Shared context includes defaults; the defaults/calendar composition test verifies saved duration reaches the actual action adapter, a current override changes that event, and the saved preference remains unchanged. Resolution scenarios exercise research-first completion and necessary clarification for missing/conflicting evidence |
| Preferences cannot expand external-action authority | Memory/defaults are untrusted guidance. Host registry permissions, calendar guest authorization, browser/publication gates and execution limits remain separate; permission, invitation, budget and uncertain-action tests run in the full suite |
| Unified active, scheduled, waiting, blocked, completed, cancelled and uncertain overview | `work_overview` projects existing tasks, inbox, objectives, schedules and effect receipts; overview tests inspect owner questions, superseded turns, handoff kind, fresh source annotations and uncertain reconciliation instructions |
| Fresh evidence before presenting old work as outstanding | Shared task-evidence refresh plus overview annotations; task-evidence tests cover later sent replies, drafts, partial sources and unavailable checks. Unchecked saved tasks are explicitly tracked history rather than claims that a response is owed |
| On-demand overview, digest reuse, concise next steps and links | Shared `work.overview` tool and compact digest projection; overview, digest, heartbeat and assistant-journey tests inspect next steps, source/conversation links and complete Slack output |
| Meaningful notifications and cross-automation deduplication | Versioned finding notices and task notices use the common delivery lock and existing ledgers; finding/attention tests cover overlapping roles, changed facts, mixed reports, rejected sends and old uncertain reservations |
| Preserve reminders and explicit updates; configurable importance/quiet periods | Owner-scoped notification preferences require a revision and durable receipt. Tests cover overnight windows, reversal, owner scope, reminders, explicit schedules and reconciliation bypass. Selected imminent events, high-priority tasks and host-classified owner-action problems bypass the hourly filter |
| Synthetic regressions represent past failures and inspect outcomes | Fixed 50-test harness plus full suite covers missed Slack work, attachments, research/date resolution, edits, multi-item completion, stale evidence, partial results and duplicate prevention; assertions inspect resulting remote state and rendered replies, not merely tool names |
| Honest comparison and measurement limits | Three samples per version, identical fixtures, verified import scope and journal-derived provider counts. Timings measure only the scripted harness; no claim of improved model judgment, production speed or token savings |
| Public repository privacy and publication checks | Synthetic test values only; preferences, prompts, transcripts, process logs, receipts and comparison artifacts stay outside Git. Staged source is inspected and scanned before each commit/push |
| Required tests and permitted deployment | `python3 -m unittest discover -s tests -q`; CI on Python 3.11/3.14; the existing updater stages, retests, drains and checks the exact active revision and Slack health |

Bounded coverage is explicit throughout: the overview scans finite ledger pages;
source refresh checks at most 20 open tasks; cross-automation matching requires
structured source/version identities. Important-only classification uses the
supported host signals above, rather than claiming to recognize every possible
urgent situation. Explicit owner-scheduled full reports are preserved. These are
implemented limits, not evidence of exhaustive personal-account monitoring.

No new external account setup is required for the consolidation. The live
reasoning primary resolves to Codex. The optional fallback remains disabled until
configured; enabling it is an owner choice, not an implicit extra subscription.
