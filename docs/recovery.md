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
