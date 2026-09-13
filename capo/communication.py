"""Short, evidence-led updates; private execution details stay in artifacts."""
import re

STYLE = (
    "Write for someone with no technical background. Use short sentences and everyday words. State the purpose or result, "
    "then the evidence that supports it. Prefer concrete verbs and measured claims. "
    "Use everyday language; omit internal worker roles, file paths, objective IDs and "
    "publication machinery. Keep each ordinary reply to two short sentences and at most 40 words. Give one clear next step when needed. Explain necessary technical terms. Provide technical detail only when requested. "
    "Do not claim tests passed or a PR exists before the supplied evidence shows it. "
)


def plan_message(objective):
    summary = ' '.join(objective['plan'].get('summary', '').split())
    if (not summary or len(summary.split()) > 40 or len(summary) > 300
            or re.search(r'[/\\]|\b(?:candidate|gateway|seeded|Codex|Grok|implementer|OBJECTIVE_ID)\b', summary, re.I)):
        summary = "I'll inspect the relevant code, make the change, and check that it works."
    return summary + " I'll share the result when it's ready."


def completed_message(objective):
    publication = objective.get('publication', {})
    merge = objective.get("merge_delivery", {})
    if merge.get("status") == "completed":
        return f"Done—merged the change and deleted its branch. {publication['pr']['url']}"
    if merge.get("status") == "blocked":
        return f"I couldn’t finish merging or branch cleanup. The saved PR needs a check: {publication['pr']['url']}"
    if publication.get('status') == 'published':
        return f"The change passed its tests and review. Proposed change: {publication['pr']['url']}"
    delivery = objective.get('routine_delivery', {})
    if delivery.get('status') in ('failed', 'started'):
        return "The change passed its checks, but I couldn't confirm it reached GitHub. Your work is saved."
    if delivery.get('status') == 'review':
        return "The change passed its checks and needs your review. " + delivery['reason'] + " Ask me to prepare the draft in this thread."
    return "The change passed its checks. Ask me to prepare it for review."
