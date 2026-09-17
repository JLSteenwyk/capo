"""Readable prose and explicit Slack links with escaped control syntax."""
import re

# Preserve code verbatim, including XML examples and Markdown operators. An
# unfinished fence protects the remainder too; model output may end mid-example.
_CODE = re.compile(r'```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|(`+)[^\n]*?\1')
_TRAILER = re.compile(r'(?:\s*</(?:document|reply|parameter|invoke|function_calls|antml:invoke|antml:function_calls)>)+(?:\s+none)?\s*\Z')


def strip_transport_tail(text):
    """Remove known generation trailers before host text is appended; preserve code."""
    end=0
    for match in _CODE.finditer(text):end=match.end()
    return text[:end]+_TRAILER.sub('',text[end:])


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
    # Repair double-escaped paragraph/list separators, not arbitrary backslash
    # sequences (paths, regexes and examples can legitimately contain them).
    # Code has already been separated; URLs must also remain byte-for-byte.
    separators = r'https?://[^\s<>]+|(?<!\\)(?:\\r\\n|\\n){2,}|(?<!\\)(?:\\r\\n|\\n)(?= *(?:[-*] |[0-9]+[.)] |#{1,6} ))'
    text = re.sub(separators, lambda m: m.group() if m.group().startswith(('http://', 'https://'))
                  else m.group().replace(r'\r\n', '\n').replace(r'\n', '\n'), text)
    text = re.sub(r'(?m)^ {0,3}#{1,6} +(.+?)(?: +#+)?$', r'\1', text)
    text = re.sub(r'\*\*([^\n*]+)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)__([^\n_]+)__(?!\w)', r'\1', text)
    text = re.sub(r'(?m)^(\s*)\* +', r'\1- ', text)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)', r'\1 (\2)', text)
    return text


def slack_text(text):
    """Render HTTP sources explicitly; all other Slack control syntax stays escaped."""
    import html
    from urllib.parse import urlsplit
    def prose(value):
        # Collapse the domain (URL) form produced by plain_text without hiding
        # arbitrary surrounding prose. Other URLs get their host as the label.
        pattern = r'(?:(?P<label>[A-Za-z0-9_.-]+) \()?(?P<url>https?://[^\s<>`]+)'
        parts=[];end=0
        for match in re.finditer(pattern,value):
            url=match['url'];tail=''
            while url and (url[-1] in '.,;!?' or (url[-1]==')' and url.count(')')>url.count('('))):
                tail=url[-1]+tail;url=url[:-1]
            try:
                parsed=urlsplit(url)
                valid=bool(parsed.hostname and not parsed.username and not parsed.password)
            except ValueError:
                valid=False
            parts.append(html.escape(value[end:match.start()],quote=False))
            if not valid:
                parts.append(html.escape(match.group(),quote=False))
            else:
                label=parsed.hostname.removeprefix('www.')
                prefix=(match['label']+' (') if match['label'] else ''
                if match['label'] and match['label'].casefold().removeprefix('www.')==label.casefold() and tail.startswith(')'):
                    prefix='';tail=tail[1:]
                parts.append(html.escape(prefix,quote=False)+'<'+html.escape(url.replace('|','%7C'),quote=False)
                             +'|'+html.escape(label,quote=False)+'>'+html.escape(tail,quote=False))
            end=match.end()
        parts.append(html.escape(value[end:],quote=False))
        return ''.join(parts)
    parts=[];end=0
    for match in _CODE.finditer(text):
        parts.extend((prose(text[end:match.start()]),html.escape(match.group(),quote=False)))
        end=match.end()
    parts.append(prose(text[end:]))
    return ''.join(parts)


def slack_chunks(text, limit=2500):
    """Keep source URLs intact at message boundaries; persist chunks for retries."""
    links=list(re.finditer(r'https?://[^\s<>`]+',text))
    chunks=[];start=0
    while start<len(text):
        end=min(start+limit,len(text))
        for link in links:
            if link.start()<end<link.end():
                end=link.start() if link.start()>start else link.end()
                break
        chunks.append(text[start:end]);start=end
    return chunks
