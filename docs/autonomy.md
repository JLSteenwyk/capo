# Following through on delegated work

Capo distinguishes tracking a task from permission to perform it. Authenticated Slack requests retain their original text and conversation privately with the task. Merely saving a task or reminder does not grant permission to execute its underlying activity.

When the owner asks Capo to do work that needs later follow-through, the shared `tasks.delegate` tool records the permitted routine actions. Capo can use that tool without another confirmation when the request already authorizes the work. The tool is available only during a direct owner request, not background research or source monitoring. Empty actions revoke execution permission. Task notes and retrieved pages cannot change grants.

Hourly checks can run delegated tasks using the same shared capability registry. Each action checks the current task status, dependencies, owner instructions and action grant. Background work cannot edit another task or use tools outside both its saved scope and current host policy. The existing calendar and draft adapters retain their own restrictions and action receipts. Sending email, purchases, scheduling-policy changes and GitHub publication do not gain implicit authorization from task delegation.

The optional private Slack configuration is:

```json
{
  "autonomy": {
    "enabled": true,
    "allowed_actions": [
      "tasks.save",
      "tasks.follow_through",
      "calendar.change",
      "mail.drafts.save"
    ],
    "max_tasks": 2,
    "max_tool_calls": 6
  }
}
```

`max_tasks` is one to five per check; `max_tool_calls` is one to ten per task attempt. The provider recovery policy separately bounds reasoning retries. Available actions must be a subset of the routine list above. Disabling autonomy stops background execution; the existing monitoring and delivery schedules remain unchanged.

One owner execution lock prevents overlapping task workers. A deferred task resumes its saved request and execution directory. Uncertain actions use the shared recovery mechanisms rather than receiving a new identity. Changed owner instructions cancel the previous checkpoint and establish a new continuation. Paused, dismissed, completed and cancelled tasks cannot start another action. Already in-flight external operations may still finish; their receipts remain available for reconciliation.

Background completion requires an intended outcome and completion evidence in the task record. Unfinished work retains a next review; if the agent omits one, the host schedules a review one day later. Failed work stops until an owner follow-up changes its scope. Completed-work notices are stored privately so a missed delivery window does not discard them; the hourly alert selector combines meaningful updates and suppresses already-delivered notices.

Execution records live under the owner's private task directory, alongside the existing task database. Do not copy these records, original requests, grants or source evidence into the public repository. See [recovery](recovery.md) for retry and process-reconciliation behavior.

## Incremental commitment monitoring

Hourly evidence includes unread Gmail messages, the next 24 hours of the primary calendar, open issues from up to ten configured repositories, and relevant existing work. Gmail unread membership is refreshed each time; immutable message headers and snippets are cached by message ID, with at most 1,000 cached entries per owner. Calendar and issue snapshots are compared by stable source reference and content version.

Only changed source versions enter commitment review. Each review handles up to 100 changed items using the configured tool budget. Older unfinished reviews resume their frozen batch; acknowledging an older version never consumes a newer update. Missing items in these bounded windows are not evidence of completion or cancellation.

The monitoring registry exposes read capabilities and `commitments.observe`. Every update must cite source references supplied by the host. It can track explicit commitments or retain uncertain candidates, but cannot delegate work, perform external writes, or reopen paused, dismissed, cancelled or completed items. Owner corrections and existing task identity should be preserved when linking evidence across integrations.

Relevant source changes can wake delegated work before its next review. The same version does not repeatedly wake it. Monitoring state, source versions and cached content remain in the owner's private task storage. A failed unchanged review stays stopped rather than spending a new retry budget every hour.

Quiet alert assessments are cached for the local day and source version. Timed items are reconsidered as they move into four-hour, one-hour or overdue windows. Selected alerts are not marked quiet before delivery, so a missed send remains eligible for a later check.

Historical source aliases are retained separately from editable task fields. Removing a source from the current view or correcting a title does not let the same source recreate a dismissed task. Existing task history is indexed once when upgrading.
