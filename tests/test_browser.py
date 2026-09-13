import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from capo import browser
from capo.conversation import _write


class BrowserCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.state = browser.create(self.home, 'Find a movie', 'https://cinema.example/', ['https://cinema.example'])
        self.page = {'url': 'https://cinema.example/checkout', 'text': 'Film Cinema Tomorrow 7pm A1 USD 15.00',
                     'elements': [{'id': '0', 'type': 'button', 'label': 'Buy tickets'},
                                  {'id': '1', 'type': 'password', 'label': 'Password'}]}
        self.action = dict(action='click', target='0', value='', message='Buy one ticket.', purchase=True,
                           item='Film', venue='Cinema', showtime='Tomorrow 7pm', seats='A1', total='15.00', currency='USD')

    def tearDown(self):
        self.temp.cleanup()

    def test_site_scope_and_source_deduplication(self):
        with self.assertRaises(ValueError):
            browser.create(self.home, 'Browse', 'https://unapproved.example/', ['https://cinema.example'])
        a = browser.create(self.home, 'Browse', 'https://cinema.example/', ['https://cinema.example'], source='event')
        b = browser.create(self.home, 'Browse', 'https://cinema.example/', ['https://cinema.example'], source='event')
        self.assertEqual(a['id'], b['id'])
        for url in ('file:///etc/passwd', 'http://cinema.example/', 'https://user:secret@cinema.example/'):
            with self.assertRaises(ValueError): browser.origin(url)

    def test_purchase_quote_requires_page_evidence(self):
        pending = browser.proposal(self.state, self.page, self.action)
        self.assertTrue(pending['is_purchase'])
        self.action['total'] = '9.00'
        with self.assertRaises(ValueError): browser.proposal(self.state, self.page, self.action)

    def test_total_cannot_match_part_of_a_larger_price(self):
        self.action['total'] = '5.00'
        with self.assertRaises(ValueError): browser.proposal(self.state, self.page, self.action)

    def test_buy_label_cannot_hide_payment(self):
        self.action.update(purchase=False, total='')
        with self.assertRaises(ValueError): browser.proposal(self.state, self.page, self.action)

    def test_sensitive_fill_is_refused(self):
        self.action.update(action='fill', target='1', value='secret', purchase=False)
        with self.assertRaises(ValueError): browser.proposal(self.state, self.page, self.action)

    def test_approval_is_exact_and_expires(self):
        pending = browser.proposal(self.state, self.page, self.action)
        self.state.update(status='awaiting_approval', pending=pending)
        directory = browser.location(self.home, self.state['id'])
        _write(directory/'state.json', self.state)
        with self.assertRaises(ValueError): browser.approve(self.home, self.state['id'], 'wrong')
        browser.approve(self.home, self.state['id'], pending['digest'])
        self.assertEqual(json.loads((directory/'approval.json').read_text())['digest'], pending['digest'])
        with patch('capo.browser.time.time', return_value=time.time()+600):
            with self.assertRaises(ValueError): browser.approve(self.home, self.state['id'], pending['digest'])

    def test_page_changes_prevent_execution(self):
        pending = browser.proposal(self.state, self.page, self.action)
        with patch('capo.browser.observe', return_value=dict(self.page, text='Price changed')):
            with self.assertRaises(ValueError): browser.execute(object(), pending)

    def test_cancel_is_durable(self):
        browser.cancel(self.home, self.state['id'])
        self.assertTrue((browser.location(self.home, self.state['id'])/'cancel.json').exists())
