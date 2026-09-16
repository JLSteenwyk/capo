# iMessage and Slack

Capo's optional BlueBubbles bridge connects one explicitly linked owner chat to the existing Slack execution queue. Claude Code, tools, memory, task state, cancellation, and publication checks remain shared. There is no second agent or independent calendar-write loop.

The bridge translates authenticated iMessage input into a canonical owner event only after checking the configured direct chat and sender. It mirrors the owner's text into Slack, queues that event once, and sends “Working on it…” through iMessage. The message's original timestamp is retained for relative-date interpretation. Bot replies from the Slack service are copied to a private transport outbox and delivered to iMessage. Messages sent by Capo's Apple Account are never accepted as owner requests.

## Requirements and setup

Use a Mac with Messages signed into **Capo's own Apple Account**. A separate macOS user account is the recommended place for this: do not sign your normal Messages account out or expose unrelated personal chats to a bridge. The account running BlueBubbles needs its documented Messages automation and database permissions. Keep that account logged in and the Mac awake. On a future dedicated machine, the iMessage bridge still needs macOS; the rest of Capo can connect to it over HTTPS.

BlueBubbles supplies the Messages bridge. The current installation path needs manual attention: Homebrew reports its cask disabled because the published release does not pass Gatekeeper. Capo does not bypass Gatekeeper, remove quarantine, disable SIP, or enable BlueBubbles' private API. Inspect the [official release](https://github.com/BlueBubblesApp/bluebubbles-server/releases/latest) and resolve installation with the owner before proceeding. Basic text and image messaging uses AppleScript; native typing indicators and reaction parity are not part of this integration.

1. In Capo's macOS account, sign into Messages with its separate Apple Account and activate iMessage.
2. Install and configure BlueBubbles after reviewing its current installation status. Keep private API features off. Use a loopback server address when Capo and BlueBubbles run on the same Mac. A remote server requires HTTPS with a valid certificate.
3. Send Capo a message from your own phone or Apple Account. In BlueBubbles, obtain the actual direct chat GUID and sender address; do not guess a phone/email alias. The bridge checks that this chat has exactly one participant and that it matches the configured owner.
4. Add the `imessage` object from [the example](../integrations/imessage/config.example.json) to your private Slack configuration. Replace placeholders and set `enabled` to true only when ready to link. Never commit the populated configuration.
5. Save the BlueBubbles server password alone in a private file such as `~/.config/capo/bluebubbles-password`, with mode `600`. Do not put it in command arguments or chat. Slack image forwarding also requires the bot's `files:write` scope, in addition to its existing image-reading scopes; reinstall the Slack app if that scope was added.
6. Check the connection without sending messages:

```sh
capo imessage-check --config /private/path/slack.json \
  --env-file /private/path/slack.env \
  --password-file /private/path/bluebubbles-password
```

7. Restart the Slack service only when idle, using the updated private configuration so it records replies for mirroring. Then run the bridge with the same Capo home:

```sh
capo --home /private/path/capo-state imessage \
  --config /private/path/slack.json \
  --env-file /private/path/slack.env \
  --password-file /private/path/bluebubbles-password
```

Do not run two bridges against the same ledger. The bridge holds an operating-system lock. Use the existing operating account and private files when configuring a service manager. No new model subscription or paid API is required by the bridge.

## Conversations

Each conversation has a stable label, such as `C0123456789`. Normal iMessage replies continue the conversation selected by your last accepted message or explicit switch. Background digests do not silently change that selection.

- `threads` lists up to ten recent owner conversations, including older Slack threads.
- `switch C0123456789` selects one of those threads without starting a task.
- `C0123456789: move it to 7` sends a request to that specific thread.
- `new: help me plan a trip` starts a separate Slack thread.

When several conversations are active, use the label so an ambiguous reply reaches the intended one. Existing Slack commands, including approval and cancellation, use the same owner/thread checks after translation. Changing your linked account, chat, or bridge origin requires deliberate reconfiguration with a new ledger; it is not inferred from incoming messages.

Synchronization starts when enabled. Old iMessages are not automatically executed or copied. Existing Slack conversation context remains available through `threads` and `switch`. Edits, deletions, reactions, native reply bubbles, and typing dots are not mirrored. The apps retain their own formatting and delivery/read indicators.

Up to four PNG, JPEG, GIF, or WebP images can accompany an iMessage request. The bridge downloads each through the authenticated fixed attachment endpoint, validates its signature and size, shares it into the matching Slack thread, and attaches its file ID to the same canonical request. Capo then uses its existing image understanding. Other attachment formats need explicit support; no file is executed. Slack-origin attachments are represented by their available links or a notice to view them in Slack, not duplicated as native iMessage attachments.

## Recovery and privacy

The private `channel-sync` SQLite ledger contains linked conversation labels, input receipts, delivery state, and message text. Back it up with the other private Capo state. Restrict access to the operating account. No public webhook endpoint is exposed. The HTTP client rejects redirects, disables ambient proxies, and removes credential-bearing URLs from errors.

Input GUIDs prevent duplicate task execution. The outbox preserves long replies across bounded message chunks. A timed-out iMessage send is reconciled against exact outgoing text in the linked chat, not blindly sent again: BlueBubbles' `tempGuid` cache is not durable idempotency. Unconfirmed or ambiguous matches remain pending. An unconfirmed Slack mirror or image upload likewise stops that input before another write is attempted. Do not delete receipts or retry the original task to repair delivery.

Inspect status locally:

```sh
capo imessage-status --config /private/path/slack.json
```

If delivery is uncertain, inspect the actual conversation and private ledger before resolving it. A bridge connection failure does not imply that a calendar change failed. The original Slack service continues running when the separate bridge is offline; its captured output waits in the outbox. A local storage failure after Slack accepts a post can leave a missing mirror, which is logged; it does not cause another Slack post. This bridge does not promise mathematically exact delivery across two independent external services.

The automated suite uses fake transport clients. It covers owner/chat scope, echo rejection, duplicate requests, shared conversation selection, image handoff, complete reply chunks, and uncertain-send reconciliation. Live BlueBubbles login, OS permissions, iMessage delivery, and end-to-end conversation switching must be checked after the owner completes setup. Do not describe these as verified by the mock tests.

Protocol references: [BlueBubbles REST API](https://docs.bluebubbles.app/server/developer-guides/rest-api-and-webhooks), [server implementation](https://github.com/BlueBubblesApp/bluebubbles-server), and [private API requirements](https://docs.bluebubbles.app/private-api/installation).
