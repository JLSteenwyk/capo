# Personal tasks

Capo and its specialists share `tasks.search`, `tasks.get`, and `tasks.save` through the capability registry. These are personal commitments, separate from coding objectives. The same task ID can link email, calendar, and project references.

Each task stores a title, notes, project, status, waiting person, priority, deadline, reminder, timezone, recurrence, dependencies, and source references. Empty strings/lists represent unset fields. Search supports active or terminal tasks and returns a continuation cursor after 100 matches. Editing requires the revision returned by a read, preventing concurrent changes from silently overwriting each other.

Dates require an explicit offset consistent with the named timezone. Ambiguous requests need clarification. Daily, weekday, weekly, and monthly recurrence use local calendar time across daylight saving changes. A spring-forward gap advances by the gap; monthly dates clamp to the next month's last day. Completing a recurring task advances its dates while preserving its ID. Cancelling stops the recurring commitment.

Tasks live in an owner-scoped private SQLite database under `CAPO_HOME/tasks`. Changes and host-generated action receipts commit together. Replaying the same action returns its existing result; reusing a receipt for a different change is refused. Dependencies must exist under the same owner and cannot form cycles. Incomplete dependencies prevent completion.

This first implementation provides task storage and shared tools. Reminder delivery and digest/weekly-plan integration are being implemented separately; a stored reminder does not yet imply a scheduled notification.
