# Capo repository instructions

This repository is public. Commit and push working code milestones frequently, as requested by the owner.

Keep credentials, tokens, private keys, actual Slack member/workspace/channel IDs, private local configuration, runtime databases, transcripts, model prompts, logs, and generated run artifacts out of Git. Store machine-specific configuration under the user's private configuration directory and runtime state under `CAPO_HOME`. Public examples must use placeholders or synthetic test values. SPARKITscience is the intentionally public default team name.

Before committing, inspect staged files and check for sensitive values. Do not print suspected secrets in tool output. Ignore rules are a convenience, not proof that a file is safe to publish. PR descriptions should summarize verification without copying private command arguments, local paths, or logs.

The platform and CLI are named Capo. The team display name is configurable and defaults to SPARKITscience. Claude Code remains the primary orchestrator.

Run `python3 -m unittest discover -s tests -q` for meaningful runtime/integration changes. Slack support is optional: install with `python3 -m pip install -e '.[slack]'` when testing the real SDK adapter. Unit tests must not invoke live providers or send Slack/GitHub messages.

Self-improvement runs in a separate candidate checkout against frozen original tests and current candidate tests. Do not weaken credential handling, permissions, execution limits, regression checks, or acceptance gates to make a task succeed. Preserve explicitly configured provider selection and surface integration failures.
