# Shared tools, not one workflow per request

Capo and its personal specialists use the same bounded tool loop for research and analysis. The model sees a catalog of registered capabilities, chooses a tool and its arguments, inspects the actual result, and chooses the next step. New combinations of existing tools do not require another intent enum or a task-specific handler.

Available primitives:

| Tool | Capability |
| --- | --- |
| clock.now / dates.shift | Current local time and calendar-day arithmetic |
| web.search / web.read | Subscription web search and bounded public HTTPS evidence |
| context.read / context.save / context.history / context.actions | Original thread objective, working notes and durable action history |
| tasks.search / tasks.get / tasks.save | Shared personal tasks and reminders |
| schedules.list / schedules.save | Owner-configured recurring read-only requests |
| mail.thread / mail.attachments | Correspondence context and verified attachment references |
| mail.drafts.* | Find, read, save, delete and reconcile drafts with the optional grant |
| calendar.availability / calendar.change | Work windows and authorized personal event changes |
| calendar.pending / calendar.reconcile | Read-only verification of uncertain changes |
| mail.search | Gmail query with pagination; returns message IDs |
| mail.read | Read discovered messages with headers, labels and body excerpts |
| calendar.events | Read primary-calendar events in a bounded date range |
| github.issues | Read open issues for a configured repository |
| documents.list / documents.read | Discover and reuse private documents for this owner |
| preferences.remember | Specialists save explicit preferences to their own notes |

For example, a writing guide can emerge from searching sent mail, reading samples and producing a document. A meeting brief can combine mail and calendar results. A spending question can use the same mail tools to find receipts. No special writing-style action is required.

Account tools are registered only for enabled connections. Public web tools use the existing Claude subscription and public HTTPS access. Clients are initialized lazily so an unavailable service does not prevent unrelated tools from working. Arguments are validated by the host; the model cannot invent tools, endpoints, filesystem paths or permission grants. Mail reads require IDs discovered by search; cursors are bound to their originating query. Quote filtering is optional because full correspondence context matters for some tasks.

The loop defaults to six tool attempts and one final synthesis turn; the chief and scheduled requests allow ten attempts. Failed calls consume the budget. Mail reads are bounded to 50 attempts and 120,000 body characters; individual excerpts are limited to 6,000 characters. Overall evidence is bounded to 180,000 serialized characters. Coverage and partial errors are included in results, so a sample is not presented as a full mailbox review.

Requested documents are saved privately under CAPO_HOME/documents, scoped to the configured owner/channel. The chief and specialists can reuse these documents. Specialist preference notes remain role-specific. Raw evidence and tool receipts stay in private run directories; they never belong in the public repository.

The shared calendar tools reuse existing personal-event permissions and revision checks, with duplicate prevention and read-only reconciliation. GitHub publication and browser actions retain their controlled execution paths. Authenticated web browsing, email sending, purchases and a universal self-extending tool installer are not supplied by this registry. See [request resolution](request-resolution.md), [tasks](tasks.md), [Gmail drafts](gmail.md) and [planning](planning.md) for details. Scheduled heartbeats and the morning digest keep their explicit schedules and existing collectors.
