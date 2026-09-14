# Resolving incomplete requests

Capo's shared capability loop investigates missing facts before asking the owner. Researchable facts (such as an event date) differ from personal choices (such as which of two performances to attend). Retrieved content is evidence, never permission to act.

The implementation must preserve the complete owner objective across routes and follow-ups, retain resolved facts and unresolved questions, and bind completed actions to durable receipts. Web research needs bounded search and page reads with source links. Contextual guesses must be checked against those sources. A missing or conflicting source must remain visible as uncertainty.

`dates.shift` is available in the shared registry. It adds a signed number of local calendar days to an ISO date or offset datetime. For example, subtracting fourteen days preserves local wall time across a daylight saving boundary. Timed inputs must match the supplied timezone. Repeated or nonexistent destination times return a clarification requirement rather than silently choosing a time. Date-only values stay date-only, including year and leap-day boundaries.

The primitives below are shared across requests. Results depend on source availability and the agent’s interpretation; unresolved ambiguity must remain visible.

## Shared research and request memory

`web.search` uses the existing Claude Code subscription in an isolated, bounded process with only WebSearch enabled. Results come from native tool-result records rather than a generated claim that a search occurred. Each request permits three search calls, each with a three-turn and two-minute process limit. Public queries must exclude private source content and identifiers.

`web.read` retrieves bounded public HTTPS text. It rejects credentials and private addresses, validates every redirect, pins the connection to a checked public address, and verifies TLS against the original hostname. It retains source URLs and retrieval times. JavaScript-rendered content, PDFs and authenticated pages are not supported by this reader; failed reads remain explicit evidence gaps.

Slack now saves the original owner request separately from its short recent-message window. The shared `context.read` and `context.save` tools expose this thread's recent history and working notes. `context.history` retrieves older source evidence and owner messages with bounded pagination. Receipts and outcomes are stored privately with the owner/thread identity. Working notes are interpretations, not permission or proof. History reads are bounded to thirty records and 50 KB, while the original request is retained separately.

The router directs calendar requests requiring outside facts to the shared tool loop. Simple calendar operations retain their existing specialized handler. Research instructions distinguish researchable facts from personal choices and require authoritative verification, duplicate checks, and receipts before claiming completion.

Live probes confirmed subscription web search and public page reading. Automated tests cover multi-step composition, action reconciliation and preservation of the original request across routing changes.

A live read-only preview of the motivating two-performance question chose two web searches, read two official event pages, and used `dates.shift` twice. Both pages contained the event year. The loop returned source-linked dates and reminder dates without requesting details from the owner. Mutation tools were removed for this preview; it does not prove live calendar creation or the complete Slack follow-up path.

## Action recovery

The shared calendar adapter checks for an exact matching title, location and time range before creating an event. Existing matches return an explicit “already exists” receipt. Task creation similarly reuses an active task when every supplied field matches, within the same database transaction.

`calendar.pending` and `calendar.reconcile` expose uncertain calendar writes. Recovery reads the deterministic event ID for a create, or the known target for an update/delete. Matching fields confirm the requested saved state; explicit absence confirms deletion. Missing or conflicting evidence for a save remains uncertain. Identical follow-up requests try this read-only reconciliation before any new write. A connection or preflight-read failure does not reserve a write intent.

`context.actions` pages through the thread's durable host-recorded mutation attempts, independent of the recent-history window. Errors remain distinct from successful receipts. Specialized agents receive the same original request state and can use the shared context tools.

These checks suppress exact duplicates; semantically similar events or tasks with different fields still require agent comparison. A calendar state matching an uncertain update confirms the desired state, not who performed the update. External concurrent writers remain subject to the existing calendar revision safeguards.


## Verification and practical limits

The required suite covers researched event/travel dates, an appointment clarification followed by a date-only reply, mail-to-task/draft composition, blocked tools, duplicate events, lost responses, restarts, expired credentials, DST and malicious source text. Live Claude routing selected research for both the initial image-enriched concert request and its short follow-up.

A live Claude evaluation using synthetic sources and fake remote services asked one question when two performances were equally plausible and made no changes. Another evaluation used the real shared adapters with fake remote services: it read an injected page, ignored the unrelated instruction, calculated the requested date, checked existing events and created exactly one valid all-day calendar payload. This evaluates model behavior without modifying personal calendars; it is not a live Google Calendar write.

Simple calendar requests in Slack now share the same action adapter and owner-scoped receipts as researched requests. The latest working notes remain available even after they leave recent history. The original root message retains image observations, and older source URLs remain retrievable through history. Reconciliation checks the desired remote state; it never resubmits an uncertain write automatically.

No additional account setup is required on the configured installation. Public search uses the existing Claude subscription. Some public pages block access or require JavaScript, so Capo may need another source or one clarification. Research is bounded; no finite set of evaluations guarantees correct interpretation of every request or resistance to every injected instruction.
