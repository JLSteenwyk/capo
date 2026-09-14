# Capo repository instructions

This repository is public. Commit and push working code milestones frequently, as requested by the owner.

Keep credentials, tokens, private keys, actual Slack member/workspace/channel IDs, private local configuration, runtime databases, transcripts, model prompts, logs, and generated run artifacts out of Git. Store machine-specific configuration under the user's private configuration directory and runtime state under `CAPO_HOME`. Public examples must use placeholders or synthetic test values. SPARKITscience is the intentionally public default team name.

Before committing, inspect staged files and check for sensitive values. Do not print suspected secrets in tool output. Ignore rules are a convenience, not proof that a file is safe to publish. PR descriptions should summarize verification without copying private command arguments, local paths, or logs.

The platform and CLI are named Capo. The team display name is configurable and defaults to SPARKITscience. Claude Code remains the primary orchestrator.

Run `python3 -m unittest discover -s tests -q` for meaningful runtime/integration changes. Slack support is optional: install with `python3 -m pip install -e '.[slack]'` when testing the real SDK adapter. Unit tests must not invoke live providers or send Slack/GitHub messages.

Self-improvement runs in a separate candidate checkout against frozen original tests and current candidate tests. Do not weaken credential handling, permissions, execution limits, regression checks, or acceptance gates to make a task succeed. Preserve explicitly configured provider selection and surface integration failures.

## Architecture: reusable capabilities first

Build shared, composable tools that Capo and its specialists can discover and combine. Before adding a feature, check whether existing tools can accomplish it. Add a missing primitive to the shared registry when needed; avoid a new intent enum, keyword branch, or dedicated handler for each user request. Keep agent roles and reusable skills separate from provider integrations.

Put access control, argument validation, execution limits, receipts, and external-action authorization in host code. Let agents choose the tool sequence from current capabilities and returned evidence. Generalization must preserve those boundaries. Keep specialized execution paths when transaction semantics, reliability, or permissions require them, and explain that need.

Test new capabilities on more than the motivating example, including composition with another tool where relevant. Repeated successful procedures can become reusable skills; they should not require another hard-coded application workflow. Treat the current tool catalog as the source of capability information, and surface actual failures instead of assuming an integration is unavailable.

See [shared capabilities](docs/capabilities.md) for the current implementation and limits. Apply this philosophy to future development and Capo's self-improvements.
