# Morning digest

Capo can send one morning digest to the configured Slack channel. The default is 7:00 a.m. America/Los_Angeles, with retries until 9:00 a.m. It uses your existing Claude Code subscription to select relevant items from fetched evidence.

The digest includes work needing attention, today's primary Google Calendar, conflicts and free gaps in a configurable 9–5 window, and events over the following seven days. Open gaps refer only to the calendar checked; they do not imply you are free across other calendars. Preparation is labeled as a suggestion. GitHub coverage is limited to configured repositories, assigned issues, requested reviews, and your open PRs.

The current news mix is one world story, one music release, and two stories across AI, scientific software, and biotech. Feeds currently include BBC World, OpenAI, Google AI, Nature Biotechnology, and releases from Hugging Face, SciPy, and Biopython. Music uses Apple's public catalog for your configured artists. World news must be at most three days old; other items at most fourteen days old. Empty or failed sources are reported without fabricated replacement stories.

## Private setup

All settings, feedback, source snapshots, and delivery receipts live under `CAPO_HOME/digest`, outside Git. Connect Calendar and Slack first. Use your private Slack configuration and owner-only Slack environment file:

```sh
capo digest-settings --config ~/.config/capo/slack.json
capo digest-settings --config ~/.config/capo/slack.json --enable --time 07:00
capo digest-preview --config ~/.config/capo/slack.json --env-file ~/.config/capo/slack.env --id first-preview
```

The preview command sends a real Slack message. Reuse the same preview ID after interruption; it resumes the existing run instead of sending another. Settings output can contain personal preferences, so do not paste it into public issues.

An optional private JSON artists file has the shape `{"artists": ["Example Artist"]}` and is loaded with `digest-settings --artists-file PATH`. For large profiles, Capo checks the first five artists every day and rotates five others. Up to 100 artists can be stored; freshness and catalog coverage are limited, and a music snippet is not guaranteed every day. Playlist imports are a starting preference profile, not proof of listening counts. Public Spotify embeds may show only the first 100 tracks; playlist membership and ordering are used as evidence, not private listening history.

## Feedback and controls

Reply in the digest's thread without an @mention:

- “More like item 2.”
- “Less AI business news.”
- “Already knew item 1.”
- “Follow biotech.”
- “Follow artist Example Artist.”
- “Digest settings.”
- “Pause my digest.”
- “Resume my digest.”
- “Send it at 7:30 am.”
- “Reset my digest preferences.”

Outside a digest thread, ask naturally in the dedicated Capo channel; `@Capo digest ...` also works. Only the configured owner can change preferences. Replayed Slack events do not apply feedback twice. Silence and thanks do not change interests. Reset restores starting preferences and delivery time while preserving the current pause state. Previously shared story URLs remain excluded. Capo can apply multiple clearly requested changes with fresh preference reads between them, and asks when feedback is materially ambiguous.

Following a topic changes ranking within the configured source coverage; it does not give Capo access to every news source. Excluded phrases are also filtered locally before selection. Story numbers are bound to their original digest, so feedback on an older digest remains meaningful.

## Reliability and operation

Daily keys use the owner's configured Slack identity and local date. Persistent records survive service restarts. Generation uses a kernel lock and can safely restart because its sources are read-only. Delivery writes an intent before posting and uses a stable Slack client message ID. If an acknowledgement is lost, Capo checks channel history before attempting another post. If history cannot be checked, it waits instead of blindly resending. Bot identity, message content, and marker must all match to reconcile a post.

No scheduled digest is posted after its morning deadline. Spring-forward times that do not exist move to the next valid minute; repeated fall-back times use the first occurrence. Generation starts five minutes before the scheduled time; a ready digest waits until delivery time. If generation or a source is slow, delivery can be a few minutes late. A stopped or sleeping computer cannot send messages; a restart within the morning window catches up, and a later restart skips stale delivery.

For persistent operation on macOS, run `capo slack-daemon --config PRIVATE_CONFIG --env-file PRIVATE_ENV` under a user LaunchAgent with RunAtLoad and KeepAlive. The environment file is parsed as literal assignments rather than executed as shell code. Keep the machine awake and the user logged in through the morning window. Google OAuth apps left in Testing may require reauthorization after seven days; failed Calendar access is explicitly reported while other digest sections remain available.

Scheduled digest generation does not edit calendar events, repositories, or tasks. Its external write is the authorized Slack digest. Replies in that thread use Capo’s shared tools: an explicit new owner request can inspect or act on connected resources under their normal permissions. Reading a digest or discussing a story grants no additional action permission.

Shared `digest.read` returns current settings, the delivered digest for this thread, and ten recent feedback receipts. `digest.change` requires an inspected settings revision and a host operation receipt. Preference updates, replay identity and acknowledgment commit together in the local ledger. A concurrent preference change requires a new read; repeating a committed operation after restart does not change its weight again. The old digest-only classifier is retained solely for already-started legacy feedback requests.
