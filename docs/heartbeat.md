# Hourly Capo checks

Enable in private Slack configuration:

```json
{"heartbeat": {"enabled": true, "start_hour": 10, "end_hour": 16, "timezone": "America/Los_Angeles"}}
```

Capo checks hourly at 10 a.m., 11 a.m., noon, 1 p.m., 2 p.m., 3 p.m., and 4 p.m., every day. The 4 p.m. check is included. Timezone-aware slots follow local daylight-saving changes. A check can begin within ten minutes of the scheduled hour; old missed slots are skipped. Checks require the computer to be awake and the service running. Set enabled to false to pause.

Each check reads unread messages among up to 20 inbox entries (headers/snippets only), the primary calendar's next 24 hours, and blocked or awaiting-input Capo objectives belonging to the configured owner. It does not sweep all Slack workspaces or all GitHub repositories. The 7 a.m. digest is separate.

Claude selects at most three actionable items. No actionable item means no Slack message. Identical selected items are suppressed for the rest of that local day; changed content may alert again. Only delivered alerts are marked seen. A private per-hour receipt and bounded generation attempts prevent repeated work after ordinary restarts. The existing durable delivery mechanism reconciles uncertain Slack posts before retrying. Alerts that miss their ten-minute delivery window are not posted late.

This is a read-only attention check, not authorization to send email, modify calendars, purchase items, or start arbitrary coding tasks. The model currently runs on Claude Code. Evidence, preferences and delivery records remain private under CAPO_HOME/heartbeat. Replies in an alert thread are accepted from the configured owner without an @mention.
