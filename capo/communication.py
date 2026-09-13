"""Short, evidence-led updates; private execution details stay in artifacts."""
import re

STYLE = (
    "Write user-facing prose in clear, connected sentences. State the purpose or result, "
    "then the evidence that supports it. Prefer concrete verbs and measured claims. "
    "Use everyday language; omit internal worker roles, file paths, objective IDs and "
    "publication machinery. Keep a plan summary to two sentences and at most 40 words. "
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
    if publication.get('status') == 'published':
        return f"The change passed the configured checks and independent review. Draft PR: {publication['pr']['url']}"
    delivery = objective.get('routine_delivery', {})
    if delivery.get('status') in ('failed', 'started'):
        return "The change passed verification, but I couldn't confirm publication of the draft PR. The work is saved; delivery needs attention."
    if delivery.get('status') == 'review':
        return "The change passed verification and needs your review. " + delivery['reason'] + " Ask me to prepare the draft in this thread."
    return "Verified changes are ready. Ask me to prepare a draft PR in this thread."
