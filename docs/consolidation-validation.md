# Consolidation validation record

The first fixed-harness comparison uses pre-consolidation revision `d49c0e2`
and the consolidation implementation through quiet-hour controls plus structured
cross-automation finding deduplication. Artifacts, implementation hashes and
machine timings are stored privately under `CAPO_HOME/migration-validation`.
No live provider or personal-service call is part of this comparison.

The same 50 tests ran three times against each implementation, in separate Python
processes using the same test sources. Coverage includes Slack outage recovery,
attachment context, a screenshot-to-three-events journey, research and date
resolution, calendar changes and uncertain writes, multi-item completion, truthful
partial outcomes, and fresh source checks for tasks associated with email replies.
Assertions inspect durable state, preserved fields, output content and duplicate
writes rather than trusting a provider's completion message.

| Implementation | Tests per sample | Samples | Failures/errors | Median harness duration |
| --- | ---: | ---: | ---: | ---: |
| Before consolidation | 50 | 3 | 0 | 0.553 seconds |
| Consolidated | 50 | 3 | 0 | 0.634 seconds |

These are host regression checks with scripted provider decisions. They support
preservation of the tested outcomes, not an improvement in model judgment or
production response speed. Harness durations include local initialization and
synthetic adapters; they exclude real model and service latency. Token usage,
live provider usage, and autonomous clarification quality were not measured.
Scripted paths can verify that research and action composition are available and
that a chosen path avoids needless confirmation; they cannot establish how often
a real model chooses that path. The small timing sample supports no latency claim.

The fixed modules are `test_slack_recovery`, `test_assistant_journeys`,
`test_resolution`, `test_calendar_actions`, `test_work_inventory`,
`test_request_outcomes`, and `test_task_evidence`. New capability tests separately
cover provider selection, bounded private Codex images, practical-default receipts,
shared specialist memory, the unified overview, quiet periods and finding delivery
across overlapping automations. This record is a milestone, not proof that every
consolidation requirement or production integration has been completed.
