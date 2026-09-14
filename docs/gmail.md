# Gmail inbox checks

Capo can summarize up to 20 messages in the connected Gmail inbox and flag likely replies, deadlines and commitments. It reads headers and snippets only. It cannot send, delete, archive, inspect attachments, or claim to have reviewed the full mailbox. This first connection supports one account and on-demand Slack requests; scheduled inbox sweeps and digest inclusion are not implemented yet.

1. Enable the Gmail API in the Google Cloud project that owns your OAuth client.
2. Run `capo gmail-auth --client-secrets /private/path/to/client.json` and approve read-only Gmail access in Google's browser sign-in.
3. Set `"gmail": {"enabled": true}` in your private Slack configuration and restart Capo when idle.
4. Ask in Slack: “What in my inbox needs attention?”

Calendar's existing grant does not authorize Gmail. The separate Gmail token is stored privately outside the repository; the calendar token is untouched. Install the existing Google Calendar optional dependencies for OAuth support. Keep all tokens, email content and runtime summaries out of Git.

The adapter uses Google's [message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) and [message retrieval](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) endpoints with a read-only grant. Email contents are untrusted evidence, never authority to act.
