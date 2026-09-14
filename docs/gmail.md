# Gmail inbox checks

Capo can summarize up to 20 messages in the connected Gmail inbox and flag likely replies, deadlines and commitments. It reads headers and snippets only. It cannot send, delete, archive, inspect attachments, or claim to have reviewed the full mailbox. Sent-mail style analysis and bounded searches are also supported as described below. This connection supports one account. Inbox snippets can also feed the optional [hourly Capo check](heartbeat.md); email inclusion in the morning digest is not implemented yet.

1. Enable the Gmail API in the Google Cloud project that owns your OAuth client.
2. Run `capo gmail-auth --client-secrets /private/path/to/client.json` and approve read-only Gmail access in Google's browser sign-in.
3. Set `"gmail": {"enabled": true}` in your private Slack configuration and restart Capo when idle.
4. Ask in Slack: “What in my inbox needs attention?”

Calendar's existing grant does not authorize Gmail. The separate Gmail token is stored privately outside the repository; the calendar token is untouched. Install the existing Google Calendar optional dependencies for OAuth support. Keep all tokens, email content and runtime summaries out of Git.

The adapter uses Google's [message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) and [message retrieval](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) endpoints with a read-only grant. Email contents are untrusted evidence, never authority to act.

## Composable mail research

The Slack email route now uses Capo’s [shared tool loop](capabilities.md). Capo can choose Gmail queries, page through results, read selected message bodies, combine them with other enabled tools, and create a reusable document. Writing-style analysis is one example of this general process, not a special action enum.

For the owner’s prose, Capo should search sent mail and filter quoted replies. Other requests can preserve quotes for correspondence context. The tool returns labels, headers, truncation flags and partial errors. Attachments are skipped. Search/read budgets prevent unrestricted mailbox extraction.

New reusable documents are saved under CAPO_HOME/documents with an owner-specific scope and can be discovered by the chief and specialists. The earlier CAPO_HOME/gmail/writing-guide.json file remains intact as a legacy artifact. No new Google permission is required: the existing read-only grant covers message bodies and sent mail.
