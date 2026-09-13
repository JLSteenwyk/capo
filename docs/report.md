# Objective reports

Use `capo.report` to inspect one objective as concise JSON without printing its full private state. Run it from a Capo checkout or an environment where Capo is installed:

```bash
python3 -m capo.report OBJECTIVE_ID --home STATE_DIRECTORY
```

Both arguments are required:

- `OBJECTIVE_ID` must be exactly 16 lowercase hexadecimal characters (`0-9`, `a-f`). Invalid IDs are rejected before reading state; the database lookup uses a SQL parameter.
- `--home STATE_DIRECTORY` must identify an existing directory containing `capo.sqlite3`. This standalone command requires an explicit home; it does not default to `CAPO_HOME`.

Use `python3 -m capo.report --help` for argument help.

For example, substituting your objective ID and state directory:

```bash
python3 -m capo.report 0123456789abcdef --home /path/to/state
```

Synthetic example output for a completed objective:

```json
{
  "id": "0123456789abcdef",
  "status": "completed",
  "team_name": "SPARKITscience",
  "calls": 4,
  "max_calls": 24,
  "round": 1,
  "max_rounds": 3,
  "active_stage": "acceptance",
  "verification": {
    "checks_passed": 2,
    "checks_total": 2,
    "reviews_approved": 1,
    "reviews_total": 1
  },
  "reviewers": [
    {"provider": "grok", "approved": true}
  ],
  "pr_url": "https://github.com/OWNER/REPO/pull/1"
}
```

The report contains only these fields:

| Field | Meaning |
| --- | --- |
| `id` | Objective ID. |
| `status` | Recorded objective status. |
| `team_name` | Recorded team display name; defaults to `SPARKITscience` when absent. |
| `calls` | Consumed provider calls. |
| `max_calls` | Configured provider-call limit. |
| `round` | Recorded round count. |
| `max_rounds` | Configured round limit. |
| `active_stage` | Last recorded stage, or `null` if absent. A terminal objective may retain its last stage. |
| `verification.checks_passed` | Number of stored checks whose `passed` flag is true. |
| `verification.checks_total` | Number of stored checks. |
| `verification.reviews_approved` | Number of stored reviews whose `approved` flag is true. |
| `verification.reviews_total` | Number of stored reviews. |
| `reviewers` | Array of objects containing only `provider` and boolean `approved`. |
| `pr_url` | Existing URL from `publication.pr.url`, or `null` when absent. |

Failed-check and unapproved-review counts are the respective totals minus passed or approved counts. Counts describe stored verification results, not a cumulative history of attempts. Before verification is recorded, all four verification counts are zero and `reviewers` is empty. Queued, completed, and blocked objectives use the same report shape. A blocked status does not itself imply a failed verification check.

Request text, prompts, file paths, raw errors, review findings, credentials, and other private state fields are omitted. Reporting does not run verification, call providers, or publish or refresh a PR.

## Read-only state access

The command never creates, modifies, or truncates files in the source state directory. It never connects SQLite directly to the source ledger. Instead, it reads the database and any WAL using read-only file access, compares file identity, size, and modification/change timestamps before and after capture, and retries if they change. This includes WAL appearance or disappearance and concurrent commits or checkpoints.

Capture is limited to five attempts and 256 MiB of combined database and WAL data. An existing rollback journal causes refusal with `capo-report: stale rollback journal`; reporting does not attempt recovery.

A stable capture is written into a private temporary directory outside the source state directory. Only the database and WAL are copied, never the source SHM file. SQLite opens the copy with `mode=ro` and `query_only=ON`, verifies `quick_check`, and performs the parameterized lookup. Any SQLite shared-memory initialization occurs in that disposable directory, which is removed afterward. The WAL is retained so committed WAL data is visible; uncommitted data is excluded.

The report reflects the captured state. Another process may continue updating the source after capture. Ordinary filesystem reads may update access times; the guarantee concerns source contents and writes by this command.

## Errors and exit codes

Success prints one JSON object to stdout and exits `0`. A blocked objective is still a successful report and exits `0`.

Report failures exit `1`, with a concise `capo-report: ...` message on stderr and no JSON report:

| Condition | Diagnostic |
| --- | --- |
| Missing state directory or ledger | `capo-report: no Capo state found` |
| Unknown valid objective ID | `capo-report: unknown objective: 0123456789abcdef` |
| Malformed objective ID | `capo-report: invalid objective id` |
| Existing rollback journal | `capo-report: stale rollback journal` |
| Repeatedly changing capture | `capo-report: could not obtain a stable state snapshot` |
| Snapshot exceeds the size bound | `capo-report: state exceeds snapshot size limit` |

Unreadable or invalid state also produces a concise error without raw exceptions, SQL text, or paths. Missing state is not initialized. Unknown IDs create no source files; their lookup may use the private temporary copy, which is cleaned up. No failure writes into the source state directory.

Argument syntax errors, such as omitting `--home`, print argparse usage and exit `2`. `--help` exits `0` without reading state.
