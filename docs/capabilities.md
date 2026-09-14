# Shared tools, not one workflow per request

Capo and its personal specialists use the same bounded tool loop for research and analysis. The model sees a catalog of registered capabilities, chooses a tool and its arguments, inspects the actual result, and chooses the next step. New combinations of existing tools do not require another intent enum or a task-specific handler.

Available primitives:

| Tool | Capability |
| --- | --- |
| clock.now | Current local date and time |
| mail.search | Gmail query with pagination; returns message IDs |
| mail.read | Read discovered messages with headers, labels and body excerpts |
| calendar.events | Read primary-calendar events in a bounded date range |
| github.issues | Read open issues for a configured repository |
| documents.list / documents.read | Discover and reuse private documents for this owner |
| preferences.remember | Specialists save explicit preferences to their own notes |

For example, a writing guide can emerge from searching sent mail, reading samples and producing a document. A meeting brief can combine mail and calendar results. A spending question can use the same mail tools to find receipts. No special writing-style action is required.

External-read tools are registered only for enabled connections. Clients are initialized lazily so an unavailable service does not prevent unrelated tools from working. Arguments are validated by the host; the model cannot invent tools, endpoints, filesystem paths or permission grants. Mail reads require IDs discovered by search; cursors are bound to their originating query. Quote filtering is optional because full correspondence context matters for some tasks.

The loop allows six tool attempts and one final synthesis turn. Failed calls consume the budget. Mail reads are bounded to 50 attempts and 120,000 body characters; individual excerpts are limited to 6,000 characters. Overall evidence is bounded to 180,000 serialized characters. Coverage and partial errors are included in results, so a sample is not presented as a full mailbox review.

Requested documents are saved privately under CAPO_HOME/documents, scoped to the configured owner/channel. The chief and specialists can reuse these documents. Specialist preference notes remain role-specific. Raw evidence and tool receipts stay in private run directories; they never belong in the public repository.

This change generalizes research and local document production. Calendar mutations, GitHub publication and browser actions still use their existing controlled execution paths. Arbitrary web browsing, email sending, purchases and a universal self-extending tool installer are not supplied by this registry. Scheduled heartbeats and the morning digest keep their explicit schedules and existing collectors.
