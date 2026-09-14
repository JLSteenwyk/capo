# Personal tasks

Capo and its specialists share `tasks.search`, `tasks.get`, and `tasks.save` through the capability registry. These are personal commitments, separate from coding objectives. The same task ID can link email, calendar, and project references.

Each task stores a title, notes, project, status, waiting person, priority, deadline, reminder, timezone, recurrence, dependencies, and source references. Empty strings/lists represent unset fields. Search supports active or terminal tasks and returns a continuation cursor after 100 matches. Editing requires the revision returned by a read, preventing concurrent changes from silently overwriting each other.

Dates require an explicit offset consistent with the named timezone. Ambiguous requests need clarification. Daily, weekday, weekly, and monthly recurrence use local calendar time across daylight saving changes. A spring-forward gap advances by the gap; monthly dates clamp to the next month's last day. Completing a recurring task advances its dates while preserving its ID. Cancelling stops the recurring commitment.

Tasks live in an owner-scoped private SQLite database under `CAPO_HOME/tasks`. Changes and host-generated action receipts commit together. Replaying the same action returns its existing result; reusing a receipt for a different change is refused. Dependencies must exist under the same owner and cannot form cycles. Incomplete dependencies prevent completion.

A private reminder ledger uses the existing Slack delivery gateway. Due reminders send once per task/reminder time, including one clearly labeled catch-up after a missed schedule. Cancellation or rescheduling suppresses pending reminders; uncertain posts are reconciled before retries. Reminder threads retain their task identity for follow-ups. The Mac must be awake with Capo running. Hourly checks retain their separate 10 a.m.–4 p.m. schedule; explicitly requested reminders can occur outside those hours.

The morning digest considers deadlines over the next seven days, high-priority tasks and waiting items. Hourly checks consider the next day, respecting the existing 10 a.m.–4 p.m. window. A shared delivery lock and sent/unconfirmed receipts prevent an unchanged task alert from appearing in both streams on the same day. A changed deadline or waiting status creates fresh evidence. Explicitly timed reminders own their alerts instead of appearing in hourly checks; planning summaries may still include those commitments. Up to 100 matching personal tasks are considered per sweep.

Live natural-language verification created a synthetic task, moved its deadline, and completed it, preserving one task ID throughout. A delivery-time recheck suppresses queued alerts for completed, cancelled or changed tasks.

## Follow-through records

Tasks can be uncertain candidates, active, waiting, paused, dismissed, completed or cancelled. Candidates, paused tasks and dismissed tasks are excluded from reminders and active summaries. They remain searchable so repeated discovery does not silently reactivate them.

`tasks.follow_through` stores an intended outcome, completion evidence, next action, next review time, assignee, original conversation, decisions and unresolved questions on the existing task. These notes do not grant permission to take external actions. `tasks.history` provides paginated revision history. All edits require the latest revision and a host action receipt. General edits preserve follow-through details.

Task reads expose unfinished dependencies and whether dependency and waiting conditions permit work. Completing a dependency changes readiness without a redundant edit to the dependent task. Linked-source creation with the same title returns the existing item, including dismissed items; agents should search and update a known task when connecting evidence from another integration. Similar titles without shared sources are not automatically merged.
