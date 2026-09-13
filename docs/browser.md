# Browser tasks

Capo can run a separate Chromium browser on the current computer. Claude reads the page and proposes one next step. The owner reviews that step in Slack; Capo carries it out only after approval. This is guided browser use, not unrestricted desktop control or a fully autonomous shopping agent.

## Install

```sh
python3 -m pip install -e '.[slack,browser]'
python3 -m playwright install chromium
```

The optional dependency is separate from the development runtime. Browser profiles, page observations, model artifacts, approvals, and diagnostics live under `CAPO_HOME/browser`, outside the repository. The browser uses its own profile, with Chromium sandboxing enabled. It does not open the owner's normal Chrome profile. Enter login or payment credentials directly in that window; do not send them through Slack.

Enable the feature in the private Slack configuration:

```json
{
  "browser": {
    "enabled": true,
    "start_url": "https://cinema.example/showtimes",
    "allowed_origins": ["https://cinema.example"],
    "preferences": {"city": "Your city"}
  }
}
```

These entries supplement the existing owner, channel, and repository settings. Allow only the theater and checkout sites you intend to use. Website content cannot change this list. Redirects, popups, or payment frames on other origins may be blocked. Preferences guide the agent; they do not replace verification of the theater and address at checkout.

## Use in Slack

Ask naturally, or use `@Capo browse: your request`. Capo asks when booking details are missing. In the same thread:

- Answer its question in plain language; an @mention is optional in the existing thread.
- Use `@Capo approve` to allow the displayed browser step.
- Use `@Capo status` for progress.
- Use `@Capo cancel` to stop.

Browser approvals and GitHub approvals are separate and resolved by their respective threads. No approval is accepted until its browser review was delivered. Every navigation, click, fill, or selection proposed by the agent requires approval in this first version. The initial configured page opens when the owner requests the task. Password and payment-card filling by the model is refused. The model does not receive input values, cookies, or storage contents.

For a purchase step, Capo requires the movie, theater, showtime, seats, total, and currency to appear in the observed page before presenting the quote. The page must still match when the approved step is executed. Approval expires after five minutes. Capo records the payment attempt before clicking and will not automatically repeat it when the result is uncertain. Always inspect the merchant receipt if a payment was interrupted.

General-purpose page interpretation cannot guarantee a merchant's meaning, fees, payment outcome, or hidden side effects. Review the exact proposed action and the visible checkout. There is no tested, merchant-specific ticket purchase adapter yet. Reserved-seat maps, general-admission wording, frames, CAPTCHA, login, popup checkout, or dynamic page changes can require human help. Do not bypass site controls. No real purchase is part of setup or validation.

## CLI and recovery

```sh
capo browser 'Read the showtimes' --url https://cinema.example/showtimes \
  --allow-origin https://cinema.example
capo browser-run TASK_ID
# From another terminal:
capo browser-status TASK_ID
capo browser-approve TASK_ID --digest EXACT_DIGEST_FROM_STATUS
capo browser-cancel TASK_ID
```

One browser task owns the profile at a time. A task is bounded to twelve model decisions, ninety seconds per model call, and twenty minutes total. The runner observes cancellation and parent death; graceful shutdown closes the browser. Started tasks are not blindly replayed after restart. Inspect the private state, browser, and merchant account before starting a replacement task after an uncertain action. CLI approvals require manual review of the recorded step; Slack additionally requires a delivered review in the same owner thread.

To move to the dedicated computer, install Capo and Chromium there, supply private configuration and provider authentication, and start Slack there after stopping this installation. Authenticate websites separately. Do not run two hosts against the same profile or copy browser cookies into Git. Native mouse/keyboard desktop control, remote display streaming, general autonomous checkout, and OS service installation remain separate work.

Playwright's [persistent browser contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) provide the separate profile. [Request interception](https://playwright.dev/python/docs/network) enforces the configured navigation origins; service workers are blocked so they cannot bypass that interception. This origin filter is not a general network sandbox.
