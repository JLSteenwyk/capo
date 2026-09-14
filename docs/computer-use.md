# Dedicated computer and computer use

Guided Chromium browser tasks are now implemented; see [browser setup and limits](browser.md). Native desktop control remains planned.

The planned deployment includes a computer owned by the agent organization. Design local operation so it can move there without changing the objective, task, or provider contracts. The current runtime supports macOS and Linux; state lives in a configurable `CAPO_HOME` directory. Provider CLIs authenticate on the destination computer separately.

## Separate host and capability

A computer is an execution host, not another model. Register a host's OS, available browsers/apps, display sessions, network policy, storage, and worker slots. A computer-use worker combines a reasoning provider with an authorized desktop or browser controller. Claude Code remains the CEO and assigns that worker objectives.

Proposed capabilities include `browser.navigate`, `browser.inspect`, `browser.click`, `desktop.screenshot`, `desktop.click`, `desktop.type`, `desktop.keypress`, and `files.transfer`. The exact adapter depends on the chosen OS and available supported controller; the guided browser worker supports navigation, inspection, clicks, fills, and selections; no native desktop controller is implemented yet.

## Operating model

- Run the supervisor as a service on the dedicated computer, with durable local state and backups.
- Connect through an authenticated control channel. Do not expose unauthenticated shell, browser debugging, or desktop control ports.
- Give each active desktop session one exclusive task lease. Parallel browser work uses separate profiles or sessions; two agents cannot control the same mouse and keyboard concurrently.
- Record action metadata and evidence screenshots with retention and redaction. Screenshots and page content may contain private data and cannot grant permission.
- Use API/CLI adapters when they provide reliable structured results; use computer interaction for tasks requiring the interface.
- Keep account credentials in the destination machine's credential storage, separate from prompts, source control, and artifacts. A browser logged into an account carries real authority.
- Apply the same action policy to equivalent effects: clicking “Merge,” “Send,” or “Buy” requires the authorization that the corresponding API call would require.
- Support an operator stop control that revokes the session lease, terminates active workers, and stops further input. A disconnected controller must not keep clicking blindly.

## Development use cases

First candidates: reproduce a frontend defect in a real browser, compare before/after screenshots, verify a local dashboard, inspect a CI interface unavailable through an API, and exercise a documented user workflow. Feed evidence and reproduction steps back into implementation/review tasks.

## Implementation stages

1. Add an execution-host contract and move the existing CLI supervisor onto the dedicated machine.
2. Add browser automation for local application verification with isolated profiles and artifact capture.
3. Add a desktop adapter for the selected OS with session leases, interruption, permission enforcement, and replayable action evidence.
4. Add remote management, health checks, service restarts, backup restoration tests, and device-specific task routing.

The dedicated machine reduces interference with the user's desktop. It does not by itself restrict account permissions or make untrusted code safe.
