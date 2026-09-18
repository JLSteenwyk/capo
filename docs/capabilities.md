# Shared tools, not one workflow per request

New ordinary Slack requests go directly to one shared reasoning and tool loop, without a preliminary domain classifier. Capo combines specialist expertise, connected sources and controlled workflow handoffs in that loop. The model sees a catalog of registered capabilities, chooses a tool and its arguments, inspects the actual result, and chooses the next step. New combinations of existing tools do not require another intent enum or a task-specific handler.

Available primitives:

| Tool | Capability |
| --- | --- |
| clock.now / dates.describe / dates.shift | Current local time, verified weekdays and timezone conversion, and calendar-day arithmetic |
| specialists.list / specialists.read / specialists.remember | Specialist expertise and existing role-scoped owner preferences |
| digest.read / digest.change | Delivered digest context, settings, and explicit owner feedback with revision checks |
| web.search / web.read | Subscription web search and bounded public HTTPS evidence |
| context.read / context.save / context.history / context.actions | Original thread objective, working notes and durable action history |
| tasks.search / tasks.get / tasks.save | Shared personal tasks and reminders |
| schedules.list / schedules.save | Owner-configured recurring read-only requests |
| mail.thread / mail.attachments / mail.attachment.read | Correspondence context, verified attachment references, and bounded text-file inspection without draft permission |
| mail.drafts.* | Find, read, save, delete and reconcile drafts with the optional grant |
| calendar.preferences / calendar.availability / calendar.change | Owner calendar choices, combined work windows, and verified personal event changes |
| calendar.pending / calendar.reconcile | Read-only verification of uncertain changes |
| mail.search | Gmail query with pagination; returns message IDs |
| mail.read | Read discovered messages with headers, labels and body excerpts |
| calendar.events | Read primary-calendar events in a bounded date range |
| calendar.calendars / calendar.inspect | Discover calendars and inspect metadata and access roles |
| calendar.search / calendar.event | Find events in a selected calendar and inspect details and edit restrictions |
| github.issues | Read open issues for a configured repository |
| github.pull_requests / github.pull_request / github.reviews | Discover and inspect current PRs and review history |
| github.workflow_runs / github.workflow_run / github.checks / github.job_log | Inspect workflow attempts, jobs, commit checks and bounded logs |
| documents.list / documents.read | Discover and reuse private documents for this owner |
| preferences.remember | Specialists save explicit preferences to their own notes |

For example, a writing guide can emerge from searching sent mail, reading samples and producing a document. A meeting brief can combine mail and calendar results. A spending question can use the same mail tools to find receipts. No special writing-style action is required.

Account tools are registered only for enabled connections. Public web tools use the existing Claude subscription and public HTTPS access. Clients are initialized lazily so an unavailable service does not prevent unrelated tools from working. Arguments are validated by the host; the model cannot invent tools, endpoints, filesystem paths or permission grants. Mail reads require IDs discovered by search; cursors are bound to their originating query. Quote filtering is optional because full correspondence context matters for some tasks.

`workers.capacity` reads shared subscription observations and refreshes supported quota sources without invoking a model. It reports remaining quota percentages, reset times, freshness, and unknown capacity explicitly. Host-side [capacity routing](subscription-capacity.md) ranks only eligible implementation workers; the chief, explicit worker pins, image requirements, and independent review remain enforced. This is one reusable resource capability available to Capo and its specialists, not a separate intent handler.

The loop defaults to six tool attempts and one final synthesis turn; the chief and scheduled requests allow ten attempts. Failed calls consume the budget. Mail reads are bounded to 50 attempts and 120,000 body characters; individual excerpts are limited to 6,000 characters. Overall evidence is bounded to 180,000 serialized characters. Coverage and partial errors are included in results, so a sample is not presented as a full mailbox review.

Requested documents are saved privately under CAPO_HOME/documents, scoped to the configured owner/channel. Their full content is also included in the reply, so an introductory sentence cannot hide the generated answer. Longer replies use Slack’s durable chunk delivery and resume after interruptions. Short status-update limits do not apply to requested lists or documents. The chief and specialists can reuse these documents. Specialist preference notes remain role-specific. Raw evidence and tool receipts stay in private run directories; they never belong in the public repository.

The shared calendar tools reuse existing personal-event permissions and revision checks, with duplicate prevention and read-only reconciliation. GitHub publication and browser actions retain their controlled execution paths. Authenticated web browsing, email sending, purchases and a universal self-extending tool installer are not supplied by this registry. See [request resolution](request-resolution.md), [tasks](tasks.md), [Gmail drafts](gmail.md) and [planning](planning.md) for details. Scheduled heartbeats and the morning digest keep their explicit schedules and existing collectors.

GitHub inspection uses fixed read-only REST endpoints through the existing CLI authentication. Repository aliases restrict access. List tools return pages of up to 20 records, with explicit coverage and next-page hints; PR and review bodies disclose truncation. Workflow inspection links jobs to their repository before logs can be read. Log reads fetch at most 1 MB and return the last 24,000 characters fetched, explicitly indicating when later source output was not fetched. Log and review text never grants execution authority. Match checks to the current PR head SHA before treating an emailed failure as resolved.

Gmail message and thread inspection share a 50-attempt, 120,000-body-character budget. A partially exhausted budget returns useful excerpts plus explicit unread IDs rather than discarding the whole thread. Results distinguish partial coverage and access/service failure categories; access failures stop further reads in that batch. A complete inspection status means every requested message was read, not that its body or attachments were fully examined. Check each message’s truncation flag. Sent labels identify candidate owner writing, not proof that signatures or quoted text were authored by the owner.

Authenticated interactive requests expose `development.list`, `development.inspect`, `development.start`, `development.prepare`, and `development.manage`, plus `browser.start` when enabled. These are handoffs to the existing isolated workflows, not direct shell or publication access. Host code reloads the stored owner event, validates thread ownership, and carries the original request into coding/browser briefs. Coding uses repository-configured checks, workers and limits. Follow-ups and cancellations require the original objective thread. Durable receipts prevent repeating the same handoff; returned queue status is not completion. Publication and browser approval commands remain outside model-selected tools. Scheduled monitoring without an authenticated interactive owner event does not gain these handoffs.

Explicit commands, thread replies without mentions, working indicators, delivery receipts and cancellation retain their existing Slack paths. Only events with an already-saved legacy classifier `started.json` use the former domain routing handlers, to preserve their in-progress state. New ordinary requests use the shared loop even when they mention calendar, email, shopping, or coding.

## Execution limits

The private Slack configuration accepts an optional `execution` object:

```json
{"execution": {"max_calls": 10, "evidence_chars": 180000}}
```

`max_calls` accepts integers from 1 to 40; `evidence_chars` accepts integers from 1,000 to 360,000. The defaults preserve the existing ten tool attempts and 180,000 evidence characters. Failed calls and discarded stale-date decisions consume attempts. A final reasoning turn can summarize completed work after tool execution stops. Per-tool data limits, permissions, provider recovery limits, and coding/browser budgets remain separate.

Limits are pinned when a request begins. Configuration changes affect new requests; they cannot reset or expand an in-progress request's budget. Existing shared checkpoints retain their original tool budget and prior evidence limit. An oversized tool result is preserved as a private run artifact and represented by an explicit partial-coverage receipt. Exhaustion survives restarts and stops further tools; a tool that already ran is not mislabeled as failed because its returned evidence was too large. Full overflow artifacts currently require local inspection. These limits govern tool attempts and retained evidence, not a precise token-cost or wall-clock allowance.

`development.prepare` reuses the publication gateway for an existing candidate in its original owner thread. It stores the exact host-generated invitation under the requesting Slack event. After shared reasoning finishes, Slack appends that invitation to the answer and delivers it through the existing durable chunk receipts. Only complete delivery of the current candidate digest binds approval; preparing, model-generated wording, failed delivery, or a stale candidate does not. The owner still approves through the existing explicit command. The tool cannot publish or merge. Existing repository policy controls whether approval subsequently opens a draft PR or merges a checked change.

Calendar discovery and inspection results include `local_dates` with calculated weekdays in the configured display timezone. Original timestamps remain in `source_times`; end boundaries are marked exclusive. `dates.describe` provides the same calculation for other tools and workflows. It accepts an ISO calendar date or an explicit-offset instant, rejects naive datetimes, and does not infer the date of an old email or screenshot. Date-only inputs are not shifted across timezones. These facts reduce model arithmetic errors; they do not by themselves validate every claim in a generated answer.

Calendar revision rejections before a write is sent are distinct from uncertain write outcomes. The adapter archives a provably unsent rejection under its existing lock, clears the stale cache, and permits a retry after a fresh read. It never clears an old pending action merely because a journal is absent, nor releases an action whose write journal already exists. Such actions still require reconciliation. The private `unsent_attempts` table retains the operation ID and request for rejected attempts.

## Shared personal memory

Capo and scheduled specialists can use `memory.search` to recall owner-stated preferences across conversations. During owner conversations, `memory.save` records an exact quote with a stable key, revision, source event and timestamp; `memory.forget` removes a preference from active recall. Corrections require the current revision, and repeated operations reuse their receipt. The agent is instructed to save explicit durable likes and dislikes without requiring a separate “remember” command. This is persistent preference learning, not model retraining. Model interpretation still determines what is relevant; saved statements never grant permissions.

Records live under `CAPO_HOME/personal-memory/<owner-hash>/memory.sqlite3`, with private directory/file permissions. Removing an active preference does not erase historical messages, backups or action receipts. Existing conversation history, specialist preferences, documents and digest preferences remain available through their own tools.

Optional `digest_briefings` in private configuration adds up to three reusable morning research sections. Each has `key`, `title` and `request`. Sections use the same web, clock, memory and digest read tools; they cannot book, buy, send messages or edit calendars. Findings require browser links and are suppressed after successful delivery using stable keys and factual versions. Failed checks are reported as incomplete. For example, a local-event section can consult saved tastes, verify upcoming dates and venues, and report only new matching events. Section settings and personal interests belong in private configuration, not public examples or logs.

Automations now receive a bounded interest context before reasoning: active shared memory, followed artists, exclusions and topic feedback. The main digest uses this context to choose relevance; personalized research and eligible scheduled assignments use it to search and explain discoveries. A snapshot is private and fixed during retries; subsequent runs load corrections and forgotten preferences afresh. Restricted assignments only receive this context when their allowed capabilities include `memory.`; ordinary unrestricted assignments receive it automatically.

Personalized monitoring findings cite a supplied interest key, describe their relationship (`direct`, `related`, or `none`) and give a short reason. The host validates the reference and labels related discoveries “You might like.” The model still judges semantic relevance, and external claims require inspected sources. Suggestions cannot write preferences during automation runs. Explicit owner feedback can update or remove a preference through the shared memory tools; silence and delivered suggestions do not establish tastes. These rules apply across domains such as music, shopping and style, while avoiding forced personalization of unrelated operational updates.

## Subscription worker delegation

`workers.delegate` lets Capo give a focused research, comparison, synthesis, or
review assignment to Grok or Codex. Roles such as Shopping Assistant remain roles;
they do not need separate Grok schedules. Claude remains the chief and receives
the worker's report and evidence for review before answering or taking action.

`auto` prefers Grok and ranks eligible workers using current quota headroom and
recent assignment completion outcomes. Two recent failed assignments pause an
automatic worker choice for fifteen minutes. An explicit `grok` or `codex` choice
stays pinned. Exhausted workers and known invalid logins are excluded; unknown
quota is not treated as a full balance. These completion statistics are a modest
reliability signal, not a measurement of factual correctness or model intelligence.

The caller supplies an objective, necessary context, and acceptance criteria.
Workers receive that focused input, not the whole conversation or access to
private accounts. They can use only `web.search`, `web.read`, `clock.now`,
`dates.describe`, and `dates.shift`, intersected with the caller's actual allowed
registry. No mutations, recursive delegation, or arbitrary host tools are exposed.
Native worker isolation remains enforced by the existing provider adapters.
Public web search currently uses Claude's subscription; Grok handles delegated
reasoning and synthesis. Selecting Grok does not convert the search backend.

Each parent request permits two distinct assignments. Each assignment permits
three host read-tool calls and up to four reasoning calls (including its final
report). These bounded worker budgets are additional to the chief's existing
budget. Identical assignments reuse their saved report, including failures;
worker selection is pinned for interrupted work. Cancellation or a superseding
owner message stops the worker at the next orchestration boundary. An already
running native model call can finish before cancellation is observed.

`workers.activity` reports recent assignments, actual providers, status, duration,
and native reasoning-call counts. Private metadata lives under
`CAPO_HOME/worker-delegations/<owner hash>`; task inputs, reports and source
receipts remain in the parent request's private execution directory. A status of
`reported_complete` means the worker reported completion, not that its answer
has independently passed review. The chief must evaluate its evidence.

The shared conversation, specialist, scheduled-request and digest research loops
use the same mechanism. Schedules with restricted `tool_prefixes` must explicitly
include `workers.` to enable delegation; the worker still inherits only the read
primitives allowed by that schedule. Existing action permissions and repository
implementation/review workflows are unchanged.


### Bounded monitoring and scan continuation

Monitoring uses the shared research loop. Host code removes exhausted adapters from
its offered tools and rejects further calls to them. Monitoring reserves the last
two tool attempts and part of the existing evidence allowance for `monitor.report`;
it does not increase the request budget. Partial findings must describe unchecked
sources. If reporting fails, the scheduler retains an explicit incomplete report
with evidence counts rather than claiming a clean check.

Adapters can expose `research_limit`, `research_state`, and
`restore_research_state`. Request checkpoints restore consumed allowances across
restarts. For recurring checks, `next_scan_state` can supply a bounded continuation
with fresh per-occurrence allowances. Gmail implements this with unread message
IDs and query-bound pagination cursors. Successful reads represent excerpts, not
complete messages or attachments. Later checks resume that backlog, then inspect
newer sources when capacity permits. Expired provider cursors still require a fresh
search; resumption does not guarantee a complete mailbox scan in one occurrence.

Continuation data stays in private runtime storage, scoped by owner, assignment,
and revision. Reports carry at most 32 KB of continuation state. Larger positions
produce an explicit gap instead of silently losing coverage. Source artifacts and
already saved findings survive report-formatting failures. No extra Slack delivery
is triggered solely by saving a continuation.
