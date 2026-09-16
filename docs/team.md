# Capo's team

Talk to Capo in Slack. Claude Code leads one shared tool loop and applies specialist expertise within it. Replies stay in the same thread. You do not need to select a model or mention a second bot.

| Agent | Responsibility | Current execution path |
| --- | --- | --- |
| Capo | Chief of staff: delegate, track objectives, calendar and morning briefing | Existing Slack, calendar, objectives and digest integrations |
| Money Saver | Subscription and spending analysis; savings suggestions | Shared tool loop with connected sources |
| Style Assistant | Outfit and wardrobe advice; clothing preferences | Shared tool loop, including extracted image evidence |
| Shopping Assistant | Product comparisons and buying criteria | Shared tool loop; live website requests use the existing browser workflow |
| Coding Agent | Repository issues, implementation, tests, reviews and publication | Existing Claude-led coding workflow and configured workers |

Examples: “Compare these subscription costs”, “What goes with this jacket?”, “Help me choose a backpack for commuting”, or “Check the open issues in my project.” Replies inside an established thread do not require an @mention.

Each personal specialist has separate private preference notes scoped to the configured owner, channel and role. Only explicitly supplied durable preferences should be remembered. Task receipts prevent duplicate model work when Slack retries a delivery. Capo can read the most recent 20 notes for a relevant role through `specialists.read`, and save explicit owner preferences through `specialists.remember`. Existing specialist notes remain in their original private databases. This is bounded preference memory, not an exhaustive personal database. Capo can combine multiple roles’ expertise and relevant notes in one request alongside owner-shared documents. Roles no longer require separate ordinary-request execution or a separate classifier call.

The personal specialists share the chief’s enabled mail, calendar, repository-read and private-document tools. They can gather evidence instead of requiring pasted data. These tools cannot cancel subscriptions, send messages or buy products; current retail prices require a separately permitted browser task. Live browser actions retain the existing browser permissions and site allowlist. A named role does not grant new account access.

Read-only Gmail inbox checks are implemented but require [Google sign-in and setup](gmail.md). Hourly inbox attention checks are supported; additional Slack workspaces, bank/insurance accounts and retail account connections still require integration work. The roster explicitly reports these limitations. The running service currently accepts only its configured owner/channel; it does not monitor every workspace. Account connection must not be inferred from a request or model response.

Personal notes and task artifacts live under CAPO_HOME/team, outside Git. Provider subscriptions and existing publication/purchase controls are unchanged. Roles contribute expertise and scoped memory; they do not create operating-system security boundaries. Legacy specialist workers remain available to finish already-started requests.

See [shared capabilities](capabilities.md) for tool composition and execution limits.

## Standing assignments and team status

Ask “What is everyone working on?” Capo uses `team.status` to read each role’s assignments, latest run receipts, next scheduled check, findings, blockers and coverage. Agents without assignments are explicitly available on demand. A scheduled time is not evidence that a check ran: `not_run`, `building`, `expired`, delivery failures and completed quiet checks remain distinct. Historical results include their revision so edits do not appear already checked. Interactive coding objectives remain in the existing development status tools.

Standing assignments reuse `schedules.save`: optional `agent` selects `capo`, `money_saver`, `style_assistant`, `shopping_assistant` or `coding`; optional `delivery` is `always` (the legacy default) or `changes`. Existing schedules keep their agent and delivery policy when an edit omits these fields. Ask Capo to change or pause an assignment in ordinary language. Scheduling requires an owner-chosen or owner-authorized time.

A change-only check uses the normal shared read-only tools and records `monitor.report`: up to ten findings with stable identity, meaningful fact version, summary and source; up to five blockers; and an explicit coverage description. New facts or blockers generate a short Slack update. Identical key/version pairs stay quiet. A clean empty report clears the prior finding set so a later recurrence can alert again. Failed or undelivered runs never become a notification baseline. Report state and baselines survive restarts; edited assignments start a new baseline. The previous report is frozen per run so retries keep the same context.

The first three new findings and two new blockers appear in Slack; additional findings remain available through `team.status`. A missing report, missing source inspection or incomplete execution cannot be silently counted as a clean check. If the provider fails after saving a report, its findings remain visible with an explicit incomplete-verification blocker. A repeated blocker can be quiet but stays visible in status. Stable finding identity and factual version selection rely on the model; wording changes or mistaken identity selection can still cause extra alerts. Source coverage is bounded by the existing tools and ten-call budget, not an exhaustive audit.

These assignments inspect connected sources; they do not gain authority to cancel subscriptions, buy products, send messages, merge PRs or execute arbitrary code. Coding uses GitHub read tools for monitoring and the existing development workflow when the owner requests action. Money Saver can inspect connected mail and supplied documents; it has no bank or retail account access. The computer must be awake and the service running. Changing a schedule stops future work and suppresses unsent old-revision results; an already in-flight message may still arrive.

Useful examples:

- “Have Money Saver check renewal emails every morning at 10. Only report new things I need to address.”
- “Have Coding Agent check my configured repositories at 4 p.m. Report current failed builds and PRs needing attention.”
- “Pause the shopping watch.”
- “Show me the full findings from Money Saver’s last check.”
