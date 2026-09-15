# Gmail inbox checks

Capo can summarize up to 20 messages in the connected Gmail inbox and flag likely replies, deadlines and commitments. The inbox summary reads headers and snippets. Shared tools also support bounded message/thread reads, sent-mail analysis, and verified attachment references. With the optional draft grant, Capo can manage drafts as described below. It cannot send email, delete or archive received messages, or claim to have reviewed the full mailbox. This connection supports one account. Inbox snippets can also feed the optional [hourly Capo check](heartbeat.md); email inclusion in the morning digest is not implemented yet.

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

## Draft permission and write boundary

The draft integration uses `gmail.compose` in addition to existing `gmail.readonly` access. Google bundles draft management and sending in that scope; Capo's host adapter exposes only create, replace, and delete draft operations. It does not expose a send endpoint. Reconnecting with `capo gmail-auth --client-secrets /path/to/client.json --drafts` requests that additional permission. Existing read-only connections continue working unchanged.

Enable `gmail.drafts: true` in the private Slack configuration after authorization. The shared registry exposes `mail.drafts.search`, `mail.drafts.read`, `mail.drafts.save`, and `mail.drafts.delete`. `mail.thread` reads correspondence context. With read-only Gmail access, `mail.attachments` discovers files in a searched message and `mail.attachment.read` inspects supported text files. These do not require draft permission. Their verified references can also be used in authorized drafts. These tools are available to Capo and specialists. Live verification passed on September 14, 2026: a clearly labeled temporary draft was created, read, updated, deleted, and confirmed absent. No email was sent. Shared-tool tests additionally cover reply threading, attachment preservation and an email-to-task-to-draft workflow.

Draft edits require the current content revision. Recipients must occur in owner text, an existing draft, or source correspondence; names alone are insufficient. Reply drafts preserve the original subject, thread ID, Message-ID references, and In-Reply-To header. Existing attachments must be explicitly preserved using their read references. Source MIME messages and drafts are capped at 5 MiB. Capo cannot attach arbitrary local paths. Concurrent edits made in Gmail after the revision check remain a limitation: the Gmail draft API does not expose a transactional compare-and-swap contract here.

External changes use private durable action receipts. An unconfirmed operation is not automatically repeated, even after restart or an identical new request. `mail.drafts.pending` lists unresolved actions. `mail.drafts.reconcile` checks for the preserved `X-Capo-Action` header from a save attempt (Google rewrites draft Message-ID headers), or an explicit not-found response after deletion, and records the confirmed outcome. Recovery scans at most ten draft headers per call and returns a continuation cursor when more drafts remain. Missing evidence leaves the operation unconfirmed without repeating it. Authentication setup errors are checked before reserving a new action.

Draft deletion also checks for absence immediately after Google's write response. Only an explicit not-found result produces a verified deletion receipt. A remaining draft, unavailable service, or inconclusive response keeps the action pending; read-only reconciliation can finish it later without another deletion. Historical completed receipts remain unchanged. Draft saves still require stronger content verification beyond their write acknowledgement and action-header reconciliation.

References: [Google's Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [draft management](https://developers.google.com/workspace/gmail/api/guides/drafts), and [reply threading requirements](https://developers.google.com/workspace/gmail/api/guides/threads).

Attachment inspection handles text MIME types (including CSV and calendar text), JSON, and XML. It returns source text as untrusted evidence; it does not execute markup, links, or embedded instructions. Binary formats such as PDFs and images return metadata with an explicit unsupported status. Each request permits ten source MIME reads of up to 5 MiB each and 120,000 decoded attachment characters, in pages of at most 12,000 characters. Start at offset `0` and follow `next_offset`; a later page remains partial coverage of the whole file. Unsupported encodings are disclosed rather than silently substituted. Attachment bytes stay in private process memory and are shared with the draft adapter without writing arbitrary local files.
