# Images in Slack

Upload an image with your request, such as “Add this reservation to my calendar” or “Explain this diagram.” Capo sends the actual image to Claude Code, then passes its observations to the appropriate task. Visual observations remain available when you reply in the same thread without another @mention. Explicit repository requests can also include screenshots.

The Slack app needs the **files:read** bot scope. Open the app's OAuth & Permissions page, add that scope, and reinstall the app in your workspace. Existing installations require this one-time change; the repository's app manifest includes the scope. Capo reports missing permission directly instead of guessing what an attachment contains.

Supported inputs are PNG, JPEG, GIF, and WebP, up to three images per request and 4 MB per image. Use a clear screenshot with legible text. PDF, HEIC, video, and animated-frame analysis are not supported by this adapter. A new upload needs an @mention unless it is a reply inside an existing Capo thread. Message edits are not treated as new commands; send a new reply after editing a request.

Only authorized owner messages contribute images. Capo retrieves file information through Slack and downloads private content using the bot token. The token is never sent to Claude. Downloads are size bounded and restricted to Slack's file host, including redirects. Images and observations stay in private runtime artifacts outside Git.

Image text is evidence, not permission to run commands. Capo asks about unclear details and preserves uncertainty. For example, “tomorrow at 6:15” in an old conversation screenshot does not establish an exact calendar date or end time. Images do not bypass calendar, publication, or purchase controls.

Claude receives native image content through its subscription CLI with tool access disabled. Capo's structured worker calls disable automatic memory extraction and hooks: a live test found that automatic memory extraction could replace the requested structured result with a retrospective summary. Capo's own private preferences and task context are passed explicitly.

The live visual test read the restaurant name, address, and reservation time from an owner-provided screenshot and identified missing date/end-time information. After reinstallation, the live app’s files:read scope and private attachment download were verified. Claude successfully interpreted the actual Slack attachment. Calendar creation from an image remains a separate action test.

If image analysis reports an expired Claude login, reconnect the subscription used by the background service. A terminal can have a working OAuth token while a background service uses an expired saved login. Service credentials must remain in owner-only private configuration outside the repository; never put them in a public manifest or command output.
