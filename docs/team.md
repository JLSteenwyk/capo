# Capo's team

Talk to Capo in Slack. Claude Code routes requests to specialists and returns their answers in the same thread. You do not need to select a model or mention a second bot.

| Agent | Responsibility | Current execution path |
| --- | --- | --- |
| Capo | Chief of staff: delegate, track objectives, calendar and morning briefing | Existing Slack, calendar, objectives and digest integrations |
| Money Saver | Subscription and spending analysis; savings suggestions | Dedicated Claude consultation using supplied evidence |
| Style Assistant | Outfit and wardrobe advice; clothing preferences | Dedicated Claude consultation, including extracted image evidence |
| Shopping Assistant | Product comparisons and buying criteria | Dedicated Claude consultation; live website requests use the existing browser workflow |
| Coding Agent | Repository issues, implementation, tests, reviews and publication | Existing Claude-led coding workflow and configured workers |

Examples: “Compare these subscription costs”, “What goes with this jacket?”, “Help me choose a backpack for commuting”, or “Check the open issues in my project.” Replies inside an established thread do not require an @mention.

Each personal specialist has separate private preference notes scoped to the configured owner, channel and role. Only explicitly supplied durable preferences should be remembered. Task receipts prevent duplicate model work when Slack retries a delivery. The most recent 20 notes are supplied to that specialist. This is bounded preference memory, not an exhaustive personal database. Specialists currently receive only their own notes plus the current thread; automatic cross-specialist task chains are not implemented.

The personal specialists can analyze information you provide and give advice. Their consultation calls have no tools and cannot independently fetch current prices, cancel subscriptions, send messages or buy products. Live browser actions retain the existing browser permissions and site allowlist. A named role does not grant new account access.

Read-only Gmail inbox checks are implemented but require [Google sign-in and setup](gmail.md). Scheduled inbox monitoring, additional Slack workspaces, bank/insurance accounts and retail account connections still require integration work. The roster explicitly reports these limitations. The running service currently accepts only its configured owner/channel; it does not monitor every workspace. Account connection must not be inferred from a request or model response.

Personal notes and task artifacts live under CAPO_HOME/team, outside Git. Provider subscriptions and existing publication/purchase controls are unchanged. These are scoped specialist workers, not separate operating-system security boundaries.
