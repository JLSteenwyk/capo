# Planning with shared tools

Capo handles on-demand daily and weekly planning through the existing capability conversation. It combines task search, calendar events/availability, Gmail search/thread reads, and saved documents. The same tools remain available for other questions and to specialist agents. Suggested work windows are not bookings; task duration, travel and unrecorded commitments need explicit assumptions or owner input.

`calendar.availability` accepts an explicit RFC3339 range of at most 31 days, local work hours, weekdays, and minimum free-window duration. It calculates overlapping events and open intervals, ignores transparent/cancelled events, and includes all-day busy events. Local wall-clock calculations preserve daylight saving transitions.

`schedules.list` and `schedules.save` manage generic recurring read-only requests. A schedule has a title, request, weekdays (0 Monday through 6 Sunday), local HH:MM time, timezone, enabled flag and catch-up window (1, 6, 12 or 24 hours). Empty ID/revision creates; editing or pausing uses the latest revision. Creating a schedule requires the owner's chosen time. Morning digest and hourly heartbeat schedules remain separate.

Scheduled requests use the shared tool loop with mutation tools removed. They can read tasks/calendar/email and save requested documents, then deliver one concise Slack result. They cannot create drafts, alter tasks, or book events. Thread replies retain the original request and result and can request changes through the normal owner-authorized conversation.

Generation is limited to two attempts per occurrence and ten tool calls per attempt. Host leases prevent concurrent workers for one occurrence. The existing Slack delivery gateway stores receipts and reconciles uncertain posts. A restart within the configured catch-up window can finish a missed run; later occurrences are skipped. Paused or revised schedules suppress undelivered old results. The Mac must be awake with Capo running.

Validation covers conflicts, all-day events, DST, changed task deadlines, schedule catch-up, read-only tool enforcement, and one delivery across restart. Broader task/digest integration and live deployment are still in progress.

A private live preview on September 14, 2026 successfully combined task search, calendar events and Gmail search/read. It exposed a double-conversion error when Google's response included both a local RFC3339 offset and a recurring event's original timezone label. Calendar tool output now normalizes both fields to the owner's timezone. A focused live recheck returned the correct local event time; an automated regression covers the conflicting source metadata.
