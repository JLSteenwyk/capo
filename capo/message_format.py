"""Readable plain-text Slack prose without interpreting model-supplied markup."""
import re

# Preserve code verbatim, including XML examples and Markdown operators. An
# unfinished fence protects the remainder too; model output may end mid-example.
_CODE = re.compile(r'```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|(`+)[^\n]*?\1')
_TRAILER = re.compile(r'(?:\s*</(?:document|invoke|function_calls|antml:invoke|antml:function_calls)>)+\s*\Z')


def plain_text(text):
    """Normalize prose once, before persisting or splitting delivery chunks.

    Only trailing orchestration closers are removed. Arbitrary XML, code, URLs,
    inequalities and Slack-looking mentions remain literal for transport escaping.
    """
    parts = []
    end = 0
    for match in _CODE.finditer(text):
        parts.append(_prose(text[end:match.start()]))
        parts.append(match.group())
        end = match.end()
    tail = _TRAILER.sub('', text[end:])
    parts.append(_prose(tail))
    return ''.join(parts)


def _prose(text):
    text = re.sub(r'(?m)^ {0,3}#{1,6} +(.+?)(?: +#+)?$', r'\1', text)
    text = re.sub(r'\*\*([^\n*]+)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)__([^\n_]+)__(?!\w)', r'\1', text)
    text = re.sub(r'(?m)^(\s*)\* +', r'\1- ', text)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', r'\1 (\2)', text)
    return text
