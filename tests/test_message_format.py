import unittest
from capo.message_format import plain_text


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
