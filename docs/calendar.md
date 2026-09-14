# Google Calendar

Ask Capo in Slack:

- “What is on my calendar tomorrow?”
- “Add a walk tomorrow from 9 to 9:30 am.”
- “Move tomorrow’s walk to 10–10:30 am.”
- “Delete tomorrow’s walk.”

Start with an @mention. Replies in that thread do not need one. Capo asks when details are missing or more than one event matches. Clear requests to create, edit, or delete one personal event run directly.

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
