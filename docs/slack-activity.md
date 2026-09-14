# Slack activity indicator

For an authorized incoming request, Capo sets Slack's native thread status to “is working on it…” before processing it. The status is refreshed at most once per minute while waiting for a reply and cleared after the reply has been delivered. Quick commands may finish before the indicator becomes noticeable. This does not add progress messages to the thread.

Slack expires native status after two minutes if it is not refreshed. That prevents a permanent stale indicator if the service stops. A status API failure does not prevent work or normal replies. Unauthorized requests never trigger status updates.

The live workspace accepted the native thread-status API using its existing bot permissions. No app reinstallation was needed. Display details depend on the Slack client. See Slack's [thread status API](https://docs.slack.dev/reference/methods/assistant.threads.setStatus/).

This indicates response processing; longer queued development objectives also retain their existing initial plan and completion messages.
