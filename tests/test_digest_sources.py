import unittest
from datetime import datetime,timezone
from unittest.mock import Mock,patch
from capo.digest_sources import feed_items,music_items,collect,canonical


class SourceTests(unittest.TestCase):
    def setUp(self):self.now=datetime(2026,9,14,tzinfo=timezone.utc)

    def test_feed_filters_stale_future_and_missing_dates(self):
        raw=b'''<rss><channel>
        <item><title>Current</title><link>https://example.org/current?tracking=yes</link><pubDate>Sun, 13 Sep 2026 12:00:00 GMT</pubDate><description>Actual update</description></item>
        <item><title>Old</title><link>https://example.org/old</link><pubDate>Sun, 01 Feb 2026 12:00:00 GMT</pubDate></item>
        <item><title>Future</title><link>https://example.org/future</link><pubDate>Sun, 20 Sep 2026 12:00:00 GMT</pubDate></item>
        <item><title>Unknown</title><link>https://example.org/unknown</link></item>
        </channel></rss>'''
        with patch('capo.digest_sources.fetch',return_value=raw):
            items=feed_items(('Example','world','world news','https://example.org/feed'),self.now)
        self.assertEqual([v['title'] for v in items],['Current'])
        self.assertEqual(items[0]['url'],'https://example.org/current')

    def test_music_requires_exact_artist_and_recent_date(self):
        import json
        album=dict(artistName='Example, Example',collectionName='Album',releaseDate='2026-09-12T00:00:00Z',collectionViewUrl='https://music.apple.com/album/123')
        with patch('capo.digest_sources.fetch',return_value=json.dumps({'results':[album,dict(album,artistName='Someone else')]}).encode()):
            items=music_items('Example, Example',self.now)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['topic'],'Example, Example')

    def test_partial_source_failure_retains_other_evidence(self):
        with patch('capo.digest_sources.GoogleCalendar') as calendar,patch('capo.digest_sources.github_attention',return_value=([],[dict(source='GitHub',status='unavailable')])),patch('capo.digest_sources.news',return_value=([{'id':'story'}],[dict(source='News',status='ok')])):
            calendar.return_value.events.side_effect=TimeoutError()
            result=collect({'calendar':{'enabled':True},'repositories':{}},{'timezone':'America/Los_Angeles'},[],self.now)
        self.assertEqual(result['news'],[{'id':'story'}])
        self.assertIn(dict(source='GitHub',status='unavailable'),result['coverage'])
        self.assertTrue(any(v['source']=='Primary Google Calendar' and v['status']=='unavailable' for v in result['coverage']))
        calendar.return_value.request.assert_not_called()
