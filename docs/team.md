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
