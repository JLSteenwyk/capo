import unittest
from capo.message_format import plain_text, strip_transport_tail


class MessageFormatTests(unittest.TestCase):
    def test_generated_document_has_readable_headings_lists_and_no_protocol_tail(self):
        text = '## Interview guide\n\n**5. Logistics checklist**\n* Confirm the time.\nSee [details](https://example.invalid/job).\n</document>\n</invoke>'
        self.assertEqual(plain_text(text), 'Interview guide\n\n5. Logistics checklist\n- Confirm the time.\nSee details (https://example.invalid/job).')

    def test_literal_xml_code_and_mentions_are_preserved(self):
        text = 'Use `**bold**` or `</invoke>`.\n```xml\n<document>**keep**</document>\n</invoke>\n```\n<@U123> <!channel> x < 5 & y > 3\n<custom>keep</custom>'
        self.assertEqual(plain_text(text), text)
        self.assertEqual(plain_text('```xml\n</document>\n</invoke>'), '```xml\n</document>\n</invoke>')

    def test_long_document_is_complete_and_cleanup_is_idempotent(self):
        text = ('**Heading**\nDetails preserved.\n' * 300)+'</document>\n</invoke>'
        clean = plain_text(text)
        self.assertEqual(clean.count('Details preserved.'), 300)
        self.assertEqual(plain_text(clean), clean)
        self.assertNotIn('</invoke>', clean)

    def test_cleanup_preserves_markdown_and_literal_none(self):
        self.assertEqual(strip_transport_tail('**Answer**</reply>\n</invoke>\n\nnone'),'**Answer**')
        self.assertEqual(strip_transport_tail('Available options: none'),'Available options: none')
        self.assertEqual(strip_transport_tail('`</parameter>`'),'`</parameter>`')
        self.assertEqual(strip_transport_tail('```xml\n</reply>\n</invoke>'),'```xml\n</reply>\n</invoke>')


class EscapedLayoutTests(unittest.TestCase):
    def test_escaped_agenda_layout_and_repeated_formatting(self):
        raw = r"Added four blocks:\n\n- Lunch: 11:30–12:30\n- Sessions: 12:30–3:30\n- Closing: 4–5\n- Reception: 5–7\n\nExisting events are unchanged."
        expected = "Added four blocks:\n\n- Lunch: 11:30–12:30\n- Sessions: 12:30–3:30\n- Closing: 4–5\n- Reception: 5–7\n\nExisting events are unchanged."
        self.assertEqual(plain_text(raw), expected)
        self.assertEqual(plain_text(expected), expected)

    def test_numbered_layout_preserves_code_paths_and_links(self):
        raw = r"Steps:\r\n1. Check\r\n2. Finish\r\n\r\n" + r"`literal\n\n- example` C:\new\notes https://example.invalid/a\n\n-b"
        expected = "Steps:\n1. Check\n2. Finish\n\n" + r"`literal\n\n- example` C:\new\notes https://example.invalid/a\n\n-b"
        self.assertEqual(plain_text(raw), expected)
        fenced = r"```text literal\n\n- example```"
        self.assertEqual(plain_text(fenced), fenced)
