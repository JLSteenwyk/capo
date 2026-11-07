# Automated reliability evidence

Capo captures terminal outcome metadata before publishing a shared-loop result,
and at the Slack delivery boundary for earlier routing/attachment failures.
Capture is best effort and cannot block a user reply. The durable inbox and
saved delivery rows remain fallback evidence for later collection. Failed
capture markers and unknown legacy outcomes are surfaced as coverage gaps.

`reliability.report` accepts days as `"1"`, `"7"`, `"30"` or `"90"`. It reports
owner-scoped request counts, reported-complete/partial/failed/pending/unknown
outcomes, requests needing input, tool-error and uncertain-action signals,
linked examples, and timing sample sizes. Median/p90 timing separates the first
response, execution and end-to-end delivery. The first response can be a progress
reply; end-to-end timing requires final delivery. Partial timelines are omitted
per metric, never treated as zero latency. Host suspension counts as wall time.

`reliability.incidents` pages the same private evidence for review. Capture keeps
hashed request identifiers, bounded tool names and counts, outcome labels, safe
failure categories, timestamps and links. It does not duplicate prompts, message
bodies, provider error text or transcripts. Existing source records remain
private and authoritative. Derived snapshots live in owner-only files under
`CAPO_HOME/reliability/<owner-hash>/`, outside Git. Other owners' timing records
are excluded from the report. Repeated terminal capture does not create another
request or overwrite the first outcome observation.

A scorecard can be automated using the existing scheduler. Optional `report_tool`
and `report_arguments` run one registered read-only report directly, without a
reasoning provider. For example, use `report_tool: "reliability.report"` and
`report_arguments: "{\"days\":\"7\"}"` in an owner-authorized schedule with
`delivery: "always"` and `tool_prefixes: ["reliability."]`. The owner chooses the
weekdays, time and timezone through the normal schedule fields. Existing settings
remain unchanged when these optional fields are omitted during edits.

The host checks the final restricted registry, rejects mutation, handoff,
verification and finalizer tools, validates arguments, and requires a nonempty
bounded `text` result. The ordinary scheduler's occurrence receipts, generation
lock, delivery freshness checks, retry limits and uncertain-send reconciliation
still apply. Pausing or changing the schedule uses the existing revision checks.
There is no new report-specific intent router or separate delivery service.

Recurring signals and linked incidents form an evidence-backed review backlog.
Capo can combine them with `experience.history/read` to inspect corrections and
propose synthetic regression cases. A captured incident or proposed lesson does
not authorize replaying an action, publishing private evidence, changing code,
relaxing a gate, or claiming model retraining. Existing development and approval
rules still apply to any later implementation work.

Reported completion is not an independent quality score. A legitimate
clarification can produce a partial/needs-input outcome. The report therefore
leaves semantic success and unnecessary-clarification rates unassessed rather
than inventing percentages. Missing or unreadable historical evidence remains
unknown, and pending requests are not automatically called failures. The inbox
scan is bounded to 5,000 recent owner messages, with partial coverage disclosed.
Automation-run counts are a separate section derived from the existing digest,
hourly and scheduled ledgers, bounded to 1,000 recent rows per ledger. Failed,
expired, incomplete-verification and uncertain-delivery records are available
through incident review. Quiet checks remain distinct from delivered reports.
This is not an exhaustive quality score or a subscription-usage meter. Detailed
automation health remains available through the existing health/team-status tools.

An expired unsent direct-report snapshot can be regenerated from current data
within the existing bounded generation attempts. An uncertain Slack send retains
its exact payload and must reconcile; the report is not regenerated or resent
in that state. The regression suite tests both once-only delivery and stale
snapshot rebuilding against synthetic data, with model calls forbidden.

Execution outcomes and Slack processing state are separate. The scorecard shows
terminal requests whose replies are still awaiting delivery, and refreshes that
state from the existing inbox ledger. A reported-complete execution therefore
does not imply that its reply was delivered. Processed legacy records without
an outcome remain unknown; they are not promoted to successful executions.
