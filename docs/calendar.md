# Google Calendar

Ask Capo in Slack:

- “What is on my calendar tomorrow?”
- “Add a walk tomorrow from 9 to 9:30 am.”
- “Move tomorrow’s walk to 10–10:30 am.”
- “Delete tomorrow’s walk.”

Write in the dedicated Capo channel; @mentions are optional, including for new conversations. Capo asks when details are missing or more than one event matches. Clear requests to create, edit, or delete one personal event run directly.

## Connect your account

Install the optional integration in Capo’s environment:

```sh
python3 -m pip install -e '.[calendar]'
```

In [Google Cloud](https://console.cloud.google.com/), create or select a project and enable **Google Calendar API**. Under **Google Auth Platform**, configure the app’s branding and audience. For an external app in Testing, add your Google account as a test user. Create an OAuth client with application type **Desktop app**.

Download that client’s JSON file to `~/.config/capo/google-calendar-client.json`. Keep it outside this public repository. Then run:

```sh
chmod 600 ~/.config/capo/google-calendar-client.json
capo calendar-auth --client-secrets ~/.config/capo/google-calendar-client.json
capo calendar-check
```

Sign in through the browser that opens. Capo saves the refresh token privately at `~/.config/capo/google-calendar-token.json`, with owner-only permissions. Never paste either JSON file into Slack, a chat, or GitHub.

Add this section to your private Slack configuration, then restart the Slack service when idle:

```json
"calendar": {
  "enabled": true,
  "timezone": "America/Los_Angeles"
}
```

Use your own IANA timezone if different. Calendar access uses the `calendar.events.owned` OAuth scope and your primary calendar. This scope allows event access on calendars you own; the adapter restricts operations to your primary calendar.

Google external apps left in Testing can require reauthorization after seven days. See Google’s [OAuth token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration) and [Calendar Python setup guide](https://developers.google.com/workspace/calendar/api/quickstart/python).

## First-version limits

Capo reads up to 100 events over a maximum 31-day window. It asks for a shorter range if results are incomplete. Recurring events appear in schedule reads, but editing recurring events, guest events, other calendars, and special event types must currently be done in Google Calendar. This version does not send invitations.

Before edits or deletions, Capo checks the event’s latest version and uses Google’s `If-Match` condition. A changed event requires a new request. Creates use a stable event ID. Every attempted write is recorded privately before execution; an interrupted request is never automatically retried. If a network failure leaves the outcome uncertain, check Google Calendar before requesting the change again.

Calendar summaries and requested changes are processed by your Claude Code subscription. Event descriptions, guest addresses, and OAuth credentials are not sent to the planner. Private request records remain under `CAPO_HOME/calendar/`. Calendar requests use up to two Claude calls, plus the normal Slack intent classification, without blocking the Slack service.

Automated tests use mocked Google responses. A successful `calendar-check` verifies read access only; live creation, editing, and deletion should be checked with an explicitly requested test event after account connection.

Capo supplies its current local clock and configured calendar timezone to conversational routing. Today and tomorrow are resolved automatically. Clarification replies continue the unfinished request without asking for permission again. Personal events with no end time use a one-hour default; creation replies show the scheduled time. Explicit durations take precedence. Newly supplied reservation screenshots are treated as current unless their content suggests an older date; conflicting dates or unclear start times still need clarification.


## Calendar discovery and event inspection

The shared tools can list accessible calendars (`calendar.calendars`), inspect metadata (`calendar.inspect`), search a selected calendar with pagination (`calendar.search`), and inspect event details (`calendar.event`). Inspection includes descriptions, attendees, organizer, recurrence, original timezone information, and current edit restrictions. Truncation and missing events are explicit.

`calendar.events` retains its primary-calendar interface for existing callers. `calendar.change` accepts an optional `calendar_id`, defaulting to primary. Other calendars must first be discovered and have the owner access role. Guests and recurring events remain outside Capo's edit policy. Calendar identity is carried through caches, mutations, duplicate checks and uncertain-action reconciliation; event IDs alone do not identify a resource across calendars. Existing receipts without a calendar ID still mean primary.

New authorizations request `calendar.readonly` alongside the existing `calendar.events.owned` scope. The added scope enables calendar discovery, metadata and shared-calendar reads; it does not broaden write permission. Existing credentials retain their original grant until the owner reconnects with `capo calendar-auth` and the private OAuth client file. Primary-calendar event access continues working with the old grant. Google may require this one-time consent before discovery works.

Shared calendar changes are verified with a fresh lookup of the intended event on the selected calendar before returning success. Create/update verification checks its ID, title, location, dates/times, and the correct all-day or timed representation. Updates also preserve previously inspected notes, reminders, attachments, visibility and other supported metadata in a private verification snapshot. Delete verification requires explicit absence. Missing, conflicting or unavailable evidence leaves the action unconfirmed; read-only reconciliation uses the saved snapshot after restart, without replaying the write. Older completed receipts retain their original evidence level and are not retroactively labeled verified.

Calendar selection can be configured privately under `calendar`:

```json
{
  "enabled": true,
  "timezone": "America/Los_Angeles",
  "default_calendar_id": "primary",
  "availability_calendar_ids": ["primary", "work@example.invalid"]
}
```

Use actual calendar IDs obtained through discovery. Defaults remain `primary`; availability defaults to the creation preference when its own list is omitted. `calendar.preferences` exposes these choices to Capo. Non-primary calendars must still be discovered, and writes still require ownership. An explicitly supplied `calendar_id` overrides the creation default. Existing update/delete callers that omit it continue targeting primary for compatibility; new requests should carry the ID from inspection.

`calendar.availability` accepts an optional list of up to ten `calendar_ids`. It merges busy intervals while retaining calendar/event identity in conflicts. Secondary calendars currently contribute one page of up to 50 events; primary retains its bounded event-read contract. Any additional page, service failure, unreadable event or unknown secondary all-day timezone yields partial coverage and no free-window claims. Inspect metadata to resolve a missing timezone. All-day blocks use their source calendar’s timezone, while free windows use the configured local timezone. No event is created by an availability check.

Availability uses timezones returned by event listings or calendar metadata. Legacy primary-calendar reads fall back to the configured local timezone when neither supplies one; the coverage record labels that assumption. Unknown secondary all-day timezones remain incomplete rather than assuming the primary timezone.
