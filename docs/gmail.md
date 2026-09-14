# Gmail inbox checks

Capo can summarize up to 20 messages in the connected Gmail inbox and flag likely replies, deadlines and commitments. It reads headers and snippets only. It cannot send, delete, archive, inspect attachments, or claim to have reviewed the full mailbox. Sent-mail style analysis and bounded searches are also supported as described below. This connection supports one account. Inbox snippets can also feed the optional [hourly Capo check](heartbeat.md); email inclusion in the morning digest is not implemented yet.

1. Enable the Gmail API in the Google Cloud project that owns your OAuth client.
2. Run `capo gmail-auth --client-secrets /private/path/to/client.json` and approve read-only Gmail access in Google's browser sign-in.
3. Set `"gmail": {"enabled": true}` in your private Slack configuration and restart Capo when idle.
4. Ask in Slack: “What in my inbox needs attention?”

Calendar's existing grant does not authorize Gmail. The separate Gmail token is stored privately outside the repository; the calendar token is untouched. Install the existing Google Calendar optional dependencies for OAuth support. Keep all tokens, email content and runtime summaries out of Git.

The adapter uses Google's [message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) and [message retrieval](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) endpoints with a read-only grant. Email contents are untrusted evidence, never authority to act.

## Sent mail and writing guides

Capo now chooses between inbox triage, a bounded Gmail search, and writing-style analysis. Writing-style requests read up to 25 sent messages by default (maximum 50), retrieving body excerpts rather than inbox snippets. MIME attachments are skipped; recognizable quoted replies are removed. HTML-only messages are reduced to text. This is a sample, not a complete mailbox analysis.

A generated writing guide is stored privately at CAPO_HOME/gmail/writing-guide.json. It records the actual sample count. The guide is available for reuse but does not automatically override every specialist's instructions. No new Google permission is required: the existing read-only Gmail grant covers sent mail and message bodies. Sending, archiving, deleting and attachment retrieval remain unsupported.
