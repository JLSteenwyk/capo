# Subscription capacity

Capo checks subscription headroom before delegating development tasks. Claude
Code remains the chief. Only the objective's allowed implementation workers can
be switched, and a reserved reviewer cannot implement. A single worker choice
stays pinned. The actual implementation providers determine independent review.

Ask Capo in Slack, “How much subscription capacity is available?” The shared
`workers.capacity` tool returns the same observations used by routing. Locally:

```sh
python3 -m capo capacity
python3 -m capo capacity --cached
```

These commands do not invoke models. The first refreshes supported quota sources;
the second reads saved observations only.

## What is measured

| Provider | Source | Limits |
| --- | --- | --- |
| Codex | `codex app-server` → `account/rateLimits/read` | Reads the default Codex quota bucket and its active windows using the CLI's existing login. No thread or turn is created. |
| Claude Code | Optional supported status-line JSON feed | Captures `rate_limits` percentages and reset times. Headless Capo calls do not run a status line. Without a feed, remaining quota is unknown. |
| Grok Build | Observed usage-limit failures | The installed CLI's session token/cost counter is not a remaining subscription balance. No supported headless remaining-quota query was found. |
| All providers | Structured usage-limit errors | Share a cooldown across objectives and reasoning calls, using a reported reset or the existing one-hour fallback when none is reported. |

These are percentages of provider quota windows, **not remaining token counts**.
The most constrained active measured window determines available headroom. A
provider's context window and session token use cannot establish subscription
capacity. Model-specific buckets do not automatically describe the default model.

Codex probes have an eight-second deadline and bounded output. They use local
stdio, do not expose a network listener, and always terminate their child process.
They do not buy credits, consume earned resets, switch authentication, or invoke a
paid API. No API key or new subscription is needed. Provider configuration may
already permit overages; Capo does not modify those settings.

## Routing and recovery

- The planner receives quota evidence, alongside the allowed worker list.
- Before each delegation, the host checks again. Sources are cached for 60 seconds
  across processes to avoid repeated probes.
- Fresh measured headroom is ranked from 0–100. Unknown capacity has a neutral
  internal score of 50, **not an asserted balance**. The planner's preferred worker
  gets a 10-point preference; substantial measured headroom can override it within
  the eligible set. Ties preserve the eligible order.
- Exhausted workers are excluded until reset. When every eligible worker is
  exhausted, development work remains queued until the earliest eligible reset.
  The Slack runner skips it in the meantime. Waiting before a worker starts does
  not consume its call budget.
- Explicit reasoning fallbacks may use the same ranking. No fallback is added by
  the router; images remain with their image-capable provider. Saved completed
  reasoning results are replayed before any new quota check.
- Each development routing decision records the preferred and selected worker,
  reason, timestamp, and quota evidence privately with the objective. Recovery
  receipts likewise retain their selected provider. Tool writes keep their
  existing receipts and authorization rules.

Positive observations become unknown after five minutes. A known exhausted window
continues blocking until its reset, even if the measurement is old. At reset it
becomes unknown until refreshed; Capo never assumes a fresh full balance. A failed
probe preserves recent evidence and returns a fixed, redacted diagnostic. It does
not turn an integration failure into a claim of unlimited capacity.

Concurrent probes are coalesced with a process lock. Quota readings are advisory:
other clients can spend the subscription between measurement and execution, and
Capo cannot reserve provider tokens. New usage-limit failures update the shared
cooldown. This is capacity routing, not a learned task-performance model or an
estimate of whether an entire task will fit into the remaining allowance.

## Optional Claude quota feed

Claude Code can pass subscription windows to a status-line command. The importer
reads only these fields and discards all session, workspace, cost, and account data:

```sh
python3 -m capo --home /absolute/private/capo-state capacity-claude
```

It accepts the status-line JSON on stdin and writes no output. Add it to an
existing trusted status-line script while preserving that script's display, for
example (use an installed `capo` executable or an absolute Python/module path):

```sh
payload=$(cat)
printf '%s' "$payload" | capo capacity-claude
# The rest of your status-line script can render from "$payload".
```

Do not replace an existing status line blindly. This feed updates during normal
interactive Claude Code use; it does not run a prompt just to measure usage.
Fields absent from the payload remain unknown. The CLI importer is deliberately
not a model-writable tool: agents cannot declare their own quota available.

State is stored with private permissions under `CAPO_HOME/capacity` (by default
`~/.local/share/capo/capacity`). Use one subscription identity per provider per
state directory. If changing accounts, clear that directory while Capo is stopped
so observations from the previous account cannot influence routing. Raw quota
responses, account identifiers, credentials, and provider diagnostics are not
stored by the probe or exposed to the model.

## Provider references

- [Codex App Server: account rate limits](https://developers.openai.com/codex/app-server/)
- [Claude Code status-line fields](https://code.claude.com/docs/en/statusline)
- [Grok Build CLI reference](https://docs.x.ai/build/cli/reference)
