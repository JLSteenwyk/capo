# Personal tasks

Capo and its specialists share `tasks.search`, `tasks.get`, and `tasks.save` through the capability registry. These are personal commitments, separate from coding objectives. The same task ID can link email, calendar, and project references.

Each task stores a title, notes, project, status, waiting person, priority, deadline, reminder, timezone, recurrence, dependencies, and source references. Empty strings/lists represent unset fields. Search supports active or terminal tasks and returns a continuation cursor after 100 matches. Editing requires the revision returned by a read, preventing concurrent changes from silently overwriting each other.

Dates require an explicit offset consistent with the named timezone. Ambiguous requests need clarification. Daily, weekday, weekly, and monthly recurrence use local calendar time across daylight saving changes. A spring-forward gap advances by the gap; monthly dates clamp to the next month's last day. Completing a recurring task advances its dates while preserving its ID. Cancelling stops the recurring commitment.

Tasks live in an owner-scoped private SQLite database under `CAPO_HOME/tasks`. Changes and host-generated action receipts commit together. Replaying the same action returns its existing result; reusing a receipt for a different change is refused. Dependencies must exist under the same owner and cannot form cycles. Incomplete dependencies prevent completion.

A private reminder ledger uses the existing Slack delivery gateway. Due reminders send once per task/reminder time, including one clearly labeled catch-up after a missed schedule. Cancellation or rescheduling suppresses pending reminders; uncertain posts are reconciled before retries. Reminder threads retain their task identity for follow-ups. The Mac must be awake with Capo running. Hourly checks retain their separate 10 a.m.–4 p.m. schedule; explicitly requested reminders can occur outside those hours.

Digest and weekly-plan integration, broader composition tests, and live deployment are still in progress.
