"""Read-only, bounded evidence collection for the morning digest."""
import hashlib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

from .calendar import GoogleCalendar
from .github import GitHub, remote_repository
from .repository import git

FEEDS = [
    ('BBC World', 'world', 'world news', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('OpenAI', 'tech', 'AI', 'https://openai.com/news/rss.xml'),
    ('Google AI', 'tech', 'AI', 'https://blog.google/technology/ai/rss/'),
    ('Hugging Face releases', 'tech', 'AI', 'https://github.com/huggingface/transformers/releases.atom'),
    ('Nature Biotechnology', 'tech', 'biotech', 'https://www.nature.com/nbt.rss'),
    ('SciPy releases', 'tech', 'scientific software', 'https://github.com/scipy/scipy/releases.atom'),
    ('Biopython releases', 'tech', 'scientific software', 'https://github.com/biopython/biopython/releases.atom'),
]


def clean(value, limit=500):
    return ' '.join(html.unescape(re.sub('<[^>]*>', ' ', value or '')).split())[:limit]


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        result = parsedate_to_datetime(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid story URL')
    return urlunsplit(('https', p.netloc.lower(), p.path.rstrip('/'), '', ''))


def fetch(url):
    req = Request(url, headers={'User-Agent': 'Capo/0.1 (+https://github.com/JLSteenwyk/capo)'})
    for attempt in range(2):
        try:
            with urlopen(req, timeout=15) as response:
                raw = response.read(2_000_001)
            break
        except HTTPError as exc:
            if attempt or (exc.code != 429 and exc.code < 500):raise
            time.sleep(1)
        except (URLError,TimeoutError):
            if attempt:raise
            time.sleep(1)
    if len(raw) > 2_000_000:
        raise ValueError('Source response too large')
    return raw


def story(title, url, published, summary, source, category, topic):
    url = canonical(url)
    return dict(id=hashlib.sha256(url.encode()).hexdigest()[:20], title=clean(title, 220),
                url=url, published=published.isoformat(), summary=clean(summary, 600),
                source=source, category=category, topic=topic)


def feed_items(feed, now):
    name, category, topic, url = feed
    root = ET.fromstring(fetch(url))
    items = []
    for element in root.iter():
        if element.tag.split('}')[-1] not in ('item', 'entry'):
            continue
        fields = {}
        for child in element:
            key = child.tag.split('}')[-1]
            if key == 'link' and child.attrib.get('href'):
                if child.attrib.get('rel', 'alternate') == 'alternate':
                    fields['link'] = child.attrib['href']
            elif child.text:
                fields[key] = child.text
        try:
            published = timestamp(fields.get('pubDate') or fields.get('published') or fields.get('date') or fields['updated'])
            age = now - published
            if not timedelta(0) <= age <= timedelta(days=3 if category == 'world' else 14):
                continue
            items.append(story(fields['title'], fields['link'], published,
                               fields.get('description') or fields.get('summary') or fields.get('content', ''),
                               name, category, topic))
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(items, key=lambda v: v['published'], reverse=True)[:15]


def music_items(artist, now):
    url = 'https://itunes.apple.com/search?' + urlencode({'term': artist, 'entity': 'album', 'limit': 100})
    result = json.loads(fetch(url))
    items = []
    for album in result.get('results', []):
        if album.get('artistName', '').casefold() != artist.casefold():
            continue
        published = timestamp(album['releaseDate'])
        if not timedelta(0) <= now - published <= timedelta(days=14):
            continue
        items.append(story(album['collectionName'], album['collectionViewUrl'], published,
                           f"{album['artistName']}: {album.get('primaryGenreName', '')}. Apple catalog release date: {published.date()}.",
                           'Apple Music catalog', 'music', artist))
    return items


def news(preferences, now):
    jobs = [(name, lambda f=f: feed_items(f, now)) for f in FEEDS for name in [f[0]]]
    artists=sorted(preferences.get('artists', []),key=lambda a: preferences.get('weights', {}).get(a,0),reverse=True)
    # Always check the top five; rotate other favorites so broad profiles stay bounded.
    rest=artists[5:]
    offset=(now.date().toordinal()*5) % max(1,len(rest))
    rotating=(rest+rest)[offset:offset+min(5,len(rest))]
    jobs += [('Music: ' + artist, lambda a=artist: music_items(a, now))
             for artist in artists[:5]+rotating]
    items, coverage = [], []
    def collect(job):
        name, fn = job
        try:
            values = fn()
            return values, dict(source=name, status='ok', count=len(values), checked_at=now.isoformat())
        except Exception:
            return [], dict(source=name, status='unavailable', checked_at=now.isoformat())
    with ThreadPoolExecutor(max_workers=5) as pool:
        for values, status in pool.map(collect, jobs):
            items.extend(values); coverage.append(status)
    return list({v['id']: v for v in items}.values()), coverage


def github_attention(config, now):
    items, coverage = [], []
    for alias, settings in config['repositories'].items():
        try:
            repo = remote_repository(git(settings['path'], 'remote', 'get-url', 'origin'))
            gh = GitHub(settings.get('github_auth', 'default'))
            # Server-side identity resolution; no token or email enters the model context.
            queries = [('Assigned issue', ['issue', 'list', '--assignee', '@me']),
                       ('Review requested', ['pr', 'list', '--search', 'review-requested:@me']),
                       ('Your open PR', ['pr', 'list', '--author', '@me'])]
            for kind, args in queries:
                rows = json.loads(gh.gh(*args, '--repo', repo, '--state', 'open', '--limit', '10',
                                       '--json', 'title,url,updatedAt'))
                for row in rows:
                    items.append(dict(id=hashlib.sha256((kind+row['url']).encode()).hexdigest()[:20],
                                      title=clean(row['title'], 180), status=kind, url=row['url'],
                                      repository=alias, updated=row['updatedAt']))
            coverage.append(dict(source='GitHub: '+alias, status='ok', checked_at=now.isoformat()))
        except Exception:
            coverage.append(dict(source='GitHub: '+alias, status='unavailable', checked_at=now.isoformat()))
    return items, coverage


def collect(config, preferences, objectives, now, home=None):
    """Sources cannot mutate events, repos or tasks; failures are independent."""
    local = now.astimezone(__import__('zoneinfo').ZoneInfo(preferences['timezone']))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    result = dict(events=[], attention=[], news=[], coverage=[], now=now.isoformat(),
                  calendar='primary Google Calendar', timezone=preferences['timezone'])
    from .attention import personal_tasks
    try:
        result['attention'].extend(personal_tasks(home,config,now))
        result['coverage'].append(dict(source='Personal tasks',status='ok',checked_at=now.isoformat()))
    except Exception:
        result['coverage'].append(dict(source='Personal tasks',status='unavailable',checked_at=now.isoformat()))
    superseded={o.get('continuation_of') for o in objectives if o.get('continuation_of')}
    for objective in objectives:
        if objective['id'] in superseded:continue
        identity = objective.get('slack', {})
        if not all(identity.get(k) == config[k] for k in ('team_id','channel_id','owner_user_id')):
            continue
        waiting_review=(objective.get('routine_delivery',{}).get('status')=='review'
                        and objective.get('publication',{}).get('status')!='published')
        if objective['status'] in ('blocked', 'awaiting_input', 'queued', 'running') or waiting_review:
            result['attention'].append(dict(id=objective['id'], title=clean(objective['request'], 180),
                                            status='Review needed' if waiting_review else objective['status'], url=''))
    def calendar_read():
        if not config.get('calendar', {}).get('enabled'):
            raise ValueError('Calendar disabled')
        return GoogleCalendar().events(start.isoformat(), (start + timedelta(days=8)).isoformat())
    with ThreadPoolExecutor(max_workers=3) as pool:
        calendar_future = pool.submit(calendar_read)
        github_future = pool.submit(github_attention, config, now)
        news_future = pool.submit(news, preferences, now)
        try:
            events = calendar_future.result()
            result['events'] = [{k: e[k] for k in ('id','summary','start','end','location','transparency') if k in e} for e in events]
            result['coverage'].append(dict(source='Primary Google Calendar',status='ok',checked_at=now.isoformat()))
        except Exception:
            result['coverage'].append(dict(source='Primary Google Calendar',status='unavailable',checked_at=now.isoformat()))
        attention, coverage = github_future.result(); result['attention'] += attention; result['coverage'] += coverage
        result['news'], coverage = news_future.result(); result['coverage'] += coverage
    result['coverage'].append(dict(source='Capo tasks', status='ok', checked_at=now.isoformat()))
    return result
