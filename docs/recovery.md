# Resuming interrupted work

Shared research saves its original input, collected evidence, tool attempts and completed answer in its private run directory. Interactive chief, specialist and inbox conversations can resume these checkpoints. Scheduled research keeps one execution directory across retries. A request lock prevents concurrent execution of the same tool loop.

Temporary connection problems and timeouts use nonblocking exponential backoff. Reported rate limits retain a supplied reset timestamp or retry delay; when neither is available, Capo waits one hour. Authentication, permission, unconfirmed cleanup and unknown failures stop rather than repeatedly guessing. Existing coding-worker process supervision remains in place.

The optional `recovery` object in private Slack configuration accepts:

```json
{
  "recovery": {
    "max_attempts": 3,
    "base_delay": 30,
    "fallbacks": {}
  }
}
```

Attempts must be between one and five; the base delay is in seconds, between one and 3,600. Fallbacks are explicitly configured maps from a provider name to other available provider names. By default, there are no fallbacks. The original provider, prompt, schema and policy are frozen for each call. A configured alternate receives the same input and cannot change the host's registered tools or write permissions. Image requests retain their image-capable provider.

A caller restart does not prove a worker stopped. Recovery probes recorded local or remote workers before starting another attempt. A completed provider response can be recovered without launching another worker. Calls that reached their attempt limit remain stopped; they do not receive a fresh budget on restart.

Before a tool runs, research records its arguments. If execution is interrupted, the attempt becomes unconfirmed evidence on resume. The loop does not blindly replay that write; calendar and draft reconciliation tools can inspect the actual service state. Successful earlier steps stay in the checkpoint. Request-scoped service caches may need fresh reads after a restart, within the original tool budget.

In an active Slack thread, “stop” or “cancel this request” cancels research without waiting for its reasoning call or retry timer. Other clear requests to stop research use the natural-language router. Cancellation is scoped to the authenticated owner and thread. A private `cancelled.json` marker stops further steps; completed actions and uncertain receipts remain available. Background task execution also checks current task state and owner authorization. Recovery itself grants no authority to monitor accounts or perform external work.

For troubleshooting, inspect private `checkpoint.json`, `receipts.json`, `retry.json`, and each reasoning step's `recovery.json`. They contain sensitive task context and must not be shared publicly. Authentication failures require reconnecting the affected service. Uncertain writes require read-only reconciliation rather than deleting their receipts and trying again.

Scheduled requests retain the owner-configured catch-up window. A missed window does not silently authorize a later delivery. The latest failed or expired scheduled run is surfaced during chief checks, and newer successful runs supersede older failures. Monitoring and background work use shared, redacted error summaries for login, permission, connection and uncertain-action problems.

Connected-service reads and writes preserve HTTP authentication, permission, temporary outage, and rate-limit signals. Research checkpoints honor `Retry-After` before continuing within the original tool budget. An unconfirmed write stays uncertain and requires reconciliation. Structured CLI errors are classified even when the worker exits unsuccessfully.

Capability updates do not replace the owner request or reset its tool budget. Version 2 research checkpoints bind the original request and budget separately from the current tool catalog and chief instructions. Each reasoning step saves its exact provider input in private `input.json`; deferred attempts keep that input and the same recovery directory, so existing worker checks and retry limits remain in force. Subsequent steps use the current catalog. A saved selection still passes current host tool validation and permissions; removed tools cannot execute. Existing successful and uncertain action receipts remain intact.

Legacy checkpoints can migrate when the original request identity is proven by their fingerprint or a saved provider prompt whose recovery hash matches. Old catalog and instruction text are used only to verify that identity. If that evidence is missing or inconsistent, Capo preserves the receipts and stops instead of starting the request over. Request-scoped discovery caches still require fresh inspection after a process restart.

## Reply delivery and process death

Interactive replies, previews and objective notices now write a private receipt before posting each Slack chunk. A crash after Slack accepts a chunk can be reconciled through its marker, bot identity, thread and exact text. Restarting the service does not resend an unconfirmed chunk. Explicit rate-limit rejection permits retry; missing history access, empty history or a different message does not prove rejection. Pending replies appear in `health.status`. Digest-style deliveries follow the same no-blind-repost rule. History inspection remains bounded and permission failures remain visible; these controls cannot guarantee recovery when Slack cannot supply confirming evidence.

The synthetic restart suite terminates a separate process immediately after its durable fake service accepts calendar creates/updates/deletes, a draft save or a Slack post. Fresh processes/adapters reconcile the saved state without another external write. These are real process-death tests against fake services, not live account fault injection.

## Evidence age and work inventory

The host timestamps successful tool results. Current calendar, task and mail-search/thread observations expire after five minutes for completion claims; GitHub inspection observations expire after fifteen minutes. A delayed continuation must refresh a cited expired observation or report the gap. Immutable documents and historical mutation receipts do not expire into a reason to repeat a write. This checks evidence age, not semantic correctness or whether an uncited claim is true.

`work.track` stores up to twenty requested outcomes in the current private request directory. For a multi-item request, read its source and declare the full set before applying changes. Expand the set by retaining existing items. Completion must account for every tracked ID with its original kind, supporting receipts or explicit unfinished work. Distinct tracked actions cannot reuse one receipt. An inventory is bookkeeping, not authorization; identifying the correct original set still requires model interpretation and independent state-based evaluation. Existing legacy requests without inventories remain labeled unassessed when they return the legacy empty outcome report.

## Quiet reliability review and prepared-report age

`health.status` includes a bounded request review, also used by the existing hourly check and morning digest. It reads the latest 500 request rows for the configured owner/channel, considers up to seven days and returns at most 20 concerns. Pending requests older than fifteen minutes are distinguished from replies waiting for delivery. Shared request checkpoints identify partial outcomes and repeated failed attempts; explicit owner-input questions and cancellations are not failures. A newer owner turn supersedes older completed replies in that thread. This is a saved-state review, not proof that a worker is stuck or that a later conversation resolved the underlying work. It grants no retry authority and starts no new notification schedule. Existing alert selection and daily deduplication apply. Concerns link back to their Slack thread without copying request text or private errors.

A shared host-owned report snapshot policy checks newly generated prepared reports before delivery. Morning digests collect current calendar, task/source and GitHub evidence after the optional research sections; their current-state snapshot has a five-minute delivery window. Expired unsent digests discard their prepared text and re-collect/recompose within the existing three-attempt and delivery-window limits. Research sections use the new attempt directory so a rebuilt digest does not silently reuse an old completed briefing. Task state is checked again immediately before delivery; changed/closed notices also invalidate any cached Slack wire text.

Hourly work can perform authorized actions, so an expired hourly report is marked incomplete rather than replaying its work. Scheduled reports use the freshness limits of their actual volatile read receipts, with a fifteen-minute fallback when none are present. Neither path rebuilds automatically through this policy. Known failed deliveries become visible to existing health checks. Once delivery is uncertain (`sending`), the exact message and receipts are preserved for reconciliation, even when its evidence ages out.

This is bounded freshness, not a transactional snapshot of external accounts: an email or event can change after a read. Linked email refresh still uses bounded metadata/snippets and does not prove that every requested outcome was completed. Unsupported or failed source checks remain explicitly unverified. Legacy prepared reports without a snapshot retain their prior delivery behavior; new generation adds the policy. No source failure is converted into a claim that nothing needs attention.
