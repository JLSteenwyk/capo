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

Authenticated interactive requests expose `development.policy`, `development.list`, `development.inspect`, `development.start`, `development.prepare`, and `development.manage`, plus `browser.start` when enabled. These are handoffs to the existing isolated workflows, not direct shell or publication access. Host code reloads the stored owner event, validates thread ownership, and carries the original request into coding/browser briefs. Coding uses repository-configured checks, workers and limits. Follow-ups and cancellations require the original objective thread. Durable receipts prevent repeating the same handoff; returned queue status is not completion. Publication and browser approval commands remain outside model-selected tools. Scheduled monitoring without an authenticated interactive owner event does not gain these handoffs.

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

### Public social research and drafting

Opt-in `social.search` supplies public X evidence through Grok's metered X Search
API. It accepts a public query, up to five account handles, and optional date bounds.
Two empty bounds default to the past three UTC calendar days through today;
explicit dates preserve historical searches, including older writing samples.
Receipts retain the actual search window. Recent posts can still describe old
events, so the post-date filter does not establish event freshness.
The same primitive supports discussion research, public writing samples, source
lookup and tweet drafting. Claude remains chief and can delegate synthesis to a
subscription worker; eligible workers receive the same scoped search tool.

Enable `social_research` with `enabled: true` and `daily_requests: 5` in private
Slack configuration. Put `{"api_key":"YOUR_XAI_API_KEY"}` in
`~/.config/capo/xai.json`, owned by the service user with mode 0600. The key is read
only by the HTTP adapter, never included in prompts or receipts. Requests go only
to the fixed xAI endpoint; redirects are rejected. No X bearer token is required.

The host permits two searches per research request and a configurable daily cap
of 1–20 attempts across the installation, resetting at midnight UTC. Reservations
are durable and include failed/interrupted requests. Each API request permits one
X search tool invocation. Identical successful queries reuse request-local cached
evidence; restart checkpoints preserve that cache and allowance. `social.status`
reports the local allowance without a paid lookup. These are request limits, not a
guaranteed dollar ceiling; xAI API usage is separate from subscriptions. Provider
usage metadata is saved privately under `CAPO_HOME/social-research`.

Search results include a bounded provider-generated summary, post links, retrieval
time, usage and coverage limits. A confirmed X search is required; incomplete API
responses cannot establish current evidence. A sample does not prove a topic is
trending, and scientific claims should be checked against primary sources. Queries
must not include private email, secrets or unpublished research. Private likes,
bookmarks, account analytics and posting are not exposed.

For personalized drafts, compose `memory.search`, the existing writing-guide
`documents` tools, `social.search`, and optional `workers.delegate`. Save explicit
owner topic/handle preferences in private memory; treat observations from public
posts as sample evidence, not inferred owner instructions. Drafts return through
Slack, with no posting action and no new schedule unless the owner requests one.


### Default drafting voice

The private owner-approved writing guide is injected into every subscription
provider, including delegated workers. Requested text on the owner's behalf uses
that voice by default; the owner need not repeat “in my voice.” An explicit request
for another style takes precedence. Adapt conventions to the medium: social posts
do not inherit email greetings, signatures, private details or unrelated opinions.
Use owner-authored samples as limited calibration evidence, not as a source of
invented first-person experiences. Revise stock promotional language into specific,
measured prose before returning drafts.

Current-discussion drafts still require current source evidence. An empty memory
search is not a tool failure: all query terms must match, so broaden the query or
list recent preferences. Claims about failed tools require actual failure receipts;
unattempted tools must not be described as unavailable. These drafting instructions
supplement source and action checks; they do not guarantee a subjective voice match.


### Topical source briefs

For current-news drafts, briefs and recommendations, build a source brief before
writing: the named development, what changed, original event date, source date,
source URL and relevance. Start with the latest 72 hours, widen to seven days if
needed, and disclose older material. Use social discussion and primary news sources
when both are requested. Each draft should have one concrete, supported angle with
dated source links beside it. Prefer fewer substantiated drafts over generic filler.
A fresh repost does not make an old announcement new, and a search sample is not
proof that a topic is trending. Evergreen and historical requests can explicitly
use a different time frame. The search adapter enforces date bounds; selection and
semantic freshness still require chief review against the original sources.

### Learning from experience

`experience.history` pages through saved requests, corrections, assistant outcomes,
working notes and action receipts across the configured owner's conversations.
`experience.read` expands a returned evidence ID. Both are read-only, owner-scoped,
bounded tools usable for retrospectives, troubleshooting and follow-through; they
never search another owner's records. This is saved Capo history, not a complete
Slack archive. IDs are not dates; undated records and truncated evidence remain
explicit coverage gaps.

A recurring request can combine these tools with `team.status`, `memory.search`
and saved documents to review recent work. Its learning report is saved privately
through the existing document mechanism. Future research is instructed to consult
relevant reviews for recurring or previously corrected work. Reviews should cite
evidence IDs, retain a bounded set of useful earlier lessons, compare proposed
changes against later outcomes, and state which improvements remain untested.

This supports persistent procedural guidance, not model retraining or automatic
proof of improvement. Generated lessons never become owner-stated preferences or
authority. Reflection does not bypass permissions, edit code, increase budgets or
change schedules. Code improvements still use the existing development, testing
and deployment workflow. Like other scheduled research, review depth is limited
by the tool-call/evidence budget and must be reported honestly.

### Task inventory, contextual feedback, and service health

`tasks.overview` groups saved active tasks across conversations into overdue,
upcoming, review-due, waiting, and unscheduled work. It reports its coverage and
returns at most 20 items per group after inspecting at most 1,000 tasks. Ordinary
unfinished tasks now appear in morning briefing evidence; they do not generate
hourly alerts unless a review is due, a deadline is near, or they are waiting or
high priority. Existing receipts, task revisions, dependency checks, and delegated
authority still control changes. Conversations are not automatically converted
into tasks; the chief uses the shared task tools for requested follow-through.

`memory.feedback` stores the owner's exact feedback together with an exact excerpt
of its subject from the current message or host-loaded conversation. Directions
are more, less, avoid, or correction. It rejects invented subjects and quotes,
requires an owner request and action receipt, and uses the same revision checks,
correction, and forgetting behavior as other shared memories. Future automation
interest snapshots include this context. Existing run snapshots remain stable
for retries. Feedback does not establish broader tastes or authorize actions.

`health.status` reports saved connection checks, their freshness, and the latest
expected occurrences of enabled assignments, morning digests, and hourly checks.
It distinguishes missing, failed, partial, quiet, and delivered runs. Schedule
inspection covers the last 32 days and up to 500 saved runs per scheduler; it
excludes retired agents and disabled assignments. A quiet run is a completed
check with no new alert, not a missing report. Pre-existing reports without a
saved outcome status may not expose incomplete reasoning through this view.

`health.check` performs one bounded read-only Gmail or Calendar probe, including
normal token refresh. It never opens sign-in pages or retries external writes.
Private observations live under `CAPO_HOME/health/<owner-hash>/checks.sqlite3`;
fixed error categories keep raw service errors and credentials out of replies.
Hourly collection updates these observations and reports operational failures
without requiring an AI call. Existing notification receipts suppress repeated
alerts within a day; morning briefing evidence also includes missing/failed
reports. Checks retry on subsequent configured runs, while existing bounded
reasoning recovery handles transient provider failures. Expired grants still
require owner sign-in, and uncertain external actions still require reconciliation.

Task summaries now refresh linked email threads before presenting saved work as
current. `tasks.refresh` exposes this source check separately so it can compose
with other tools. It reads up to 20 source references per call, returns the latest
eight thread snippets, and marks unavailable, unsupported, or budget-limited
sources unverified. Gmail thread IDs found by these checks are registered with
the request's mail reader so the agent can inspect full message excerpts through
`mail.thread` within its existing limits. Drafts do not count as sent replies;
later sent messages are evidence of a reply, not automatic proof of task completion.
Other source types require their existing shared tools for verification.

Hourly monitoring refreshes linked task threads independently of the recent sent
mail sample, rotating by last inspection. Changed thread evidence reaches the
existing commitment reviewer. It reviews one linked task's source changes per
bounded pass and does not acknowledge unfinished reviews. Morning and interactive
summaries distinguish historical task status from current source evidence, and
hourly alerts reload tasks after reconciliation rather than using an earlier list.
Owner-paused and closed tasks remain protected by the existing update checks.

### Background recovery and completion

Temporary provider failures in scheduled requests resume the same occurrence and
checkpoint for at most 30 minutes beyond the original catch-up window. This grace
period is pinned once, survives restarts, and cannot expand with each retry.
Existing tool budgets, provider retry limits, action receipts, schedule revisions
and disabled-agent rules still apply. Partial results are not blindly restarted.
Partial reports preserve the actual missing-source or completion reason rather
than labeling every partial outcome as budget exhaustion. Health notices expose
those specific blockers. Worker timeouts check both elapsed and wall-clock time
so a suspended computer cannot silently extend a reasoning attempt on wake.

Commitment reviews process one linked task (with its source evidence) or one
unlinked observation per pass; remaining observations stay unacknowledged for
later checks. The shared tool loop supports optional settlement tools, currently
used for recording commitments: the last two tool calls are reserved for saving
supported updates, without requiring a mutation when no update is warranted.
This keeps exploratory reads from consuming the entire review budget.

Routine scheduled inspections and commitment reviews explicitly use medium Claude
reasoning effort to fit their short per-step deadlines. Interactive and coding
work retains its existing defaults. Provider selection, evidence requirements
and completion checks are unchanged.
