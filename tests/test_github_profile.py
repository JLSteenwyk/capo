import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.parse import urlsplit,parse_qs
from capo.github_profile import GitHubProfile,settings
from capo.github_tools import GitHubTools
from capo.capabilities import Documents,shared_tools
from capo.research_tools import ReadTools


def policy():
    return settings({'github_profile':{'enabled':True,'login':'example','auth':'keyring',
        'excluded_owners':['Excluded-Org'],'excluded_terms':['excluded company']}})


def repo(name,**kwargs):return dict(full_name=name,fork=False,archived=False,**kwargs)


class ProfileTests(unittest.TestCase):
    def test_notifications_include_browser_links_after_scope_checks(self):
        client=Mock()
        rows=[{'repository':repo('example/project'),'subject':subject} for subject in (
            {'type':'PullRequest','url':'https://api.github.com/repos/example/project/pulls/42'},
            {'type':'Issue','url':'https://api.github.com/repos/example/project/issues/9'},
            {'type':'RepositoryVulnerabilityAlert','url':None},
            {'type':'Unknown','url':'https://untrusted.example/repos/example/project/pulls/1'})]
        def get(endpoint,**kwargs):
            if endpoint=='user':value={'login':'example'}
            elif endpoint.startswith('notifications?'):value=rows
            elif endpoint=='repos/example/project':value=repo('example/project')
            else:self.fail(endpoint)
            return json.dumps(value),False
        client.get.side_effect=get
        with tempfile.TemporaryDirectory() as tmp:
            result=GitHubProfile(policy(),Path(tmp),lambda auth:client).activity('notifications','1')
        self.assertEqual([v['html_url'] for v in result['items']],[
            'https://github.com/example/project/pull/42','https://github.com/example/project/issues/9',
            'https://github.com/example/project/security/dependabot','https://github.com/example/project'])

    def test_discovery_filters_organizations_related_forks_and_terms_before_output(self):
        client=Mock();calls=[]
        def get(endpoint,**kwargs):
            calls.append(endpoint)
            if endpoint=='user':value={'login':'example'}
            elif endpoint.startswith('user/repos?'):
                value=[repo('example/ok'),repo('Excluded-Org/private'),dict(full_name='example/fork',fork=True),
                       repo('example/related',description='Work for Excluded Company')]
            elif endpoint=='repos/example/fork':value={'full_name':'example/fork','parent':{'full_name':'Excluded-Org/original'}}
            else:self.fail(endpoint)
            return json.dumps(value),False
        client.get.side_effect=get
        with tempfile.TemporaryDirectory() as tmp:
            profile=GitHubProfile(policy(),Path(tmp),lambda auth:client)
            rows=profile.repositories('1')['repositories']
            self.assertEqual([r['full_name'] for r in rows],['example/ok'])
            self.assertFalse(any(x.startswith('repos/Excluded-Org') for x in calls))
            with self.assertRaises(PermissionError):profile.authorize('eXcLuDeD-oRg/private')
            with self.assertRaises(PermissionError):profile.authorize('example/fork',configured=True)
            with self.assertRaises(ValueError):profile.authorize('unknown/project')

    def test_profile_requires_matching_authentication_and_valid_settings(self):
        client=Mock();client.get.return_value=(json.dumps({'login':'someone-else'}),False)
        with tempfile.TemporaryDirectory() as tmp:
            profile=GitHubProfile(policy(),Path(tmp),lambda auth:client)
            with self.assertRaises(PermissionError):profile.repositories('1')
            client.get.assert_called_once_with('user')
        for value in ('https://evil','a/b','--help with space'):
            with self.assertRaises(ValueError):settings({'github_profile':{'enabled':True,'login':value}})

    def test_activity_filters_excluded_content_before_metadata_reads(self):
        client=Mock();calls=[]
        def get(endpoint,**kwargs):
            calls.append(endpoint)
            if endpoint=='user':v={'login':'example'}
            elif endpoint.startswith('search/issues?'):
                self.assertIn('-org:excluded-org',parse_qs(urlsplit(endpoint).query)['q'][0])
                v={'items':[{'title':'excluded secret','repository_url':'https://api.github.com/repos/Excluded-Org/private'},
                            {'title':'Allowed issue','number':1,'repository_url':'https://api.github.com/repos/community/project'}], 'total_count':200}
            elif endpoint=='repos/community/project':v=repo('community/project')
            else:self.fail(endpoint)
            return json.dumps(v),False
        client.get.side_effect=get
        with tempfile.TemporaryDirectory() as tmp:
            profile=GitHubProfile(policy(),Path(tmp),lambda auth:client)
            result=profile.activity('involved','1')
            self.assertEqual(result['next_page'],'2')
            self.assertEqual(result['items'][0]['repository'],'community/project')
            self.assertNotIn('excluded secret',json.dumps(result))
            self.assertEqual(profile.authorize('community/project')[0],'community/project')
            self.assertFalse(any(x.startswith('repos/Excluded-Org') for x in calls))

    def test_native_tools_enforce_scope_aliases_payloads_and_pagination(self):
        client=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            profile=GitHubProfile(policy(),Path(tmp),lambda auth:client)
            profile.known.add('example/ok');profile.checked['example/ok']=True
            adapter=GitHubTools({'blocked':{'path':'/synthetic'}},lambda auth:client,profile=profile)
            with patch('capo.github_tools.git',return_value='https://github.com/Excluded-Org/private'):
                with self.assertRaises(PermissionError):adapter.issues('blocked')
            client.get.assert_not_called()
            client.get.return_value=(json.dumps([{'number':i,'title':'Excluded Company secret'} for i in range(20)]),False)
            result=adapter.pull_requests('example/ok','open','1')
            self.assertEqual(result['pull_requests'],[]);self.assertEqual(result['next_page'],'2')
            client.get.return_value=(json.dumps({'body':'Related to Excluded Company','number':1}),False)
            with self.assertRaises(PermissionError):adapter.pull_request('example/ok','1')
            for name in ('example/../private','https://evil','Excluded-Org/private'):
                with self.assertRaises((ValueError,PermissionError)):adapter.issues(name)

    def test_overview_rotates_all_active_repositories_and_keeps_coverage(self):
        calls=[];client=Mock()
        repositories=[repo('example/project'+str(i).zfill(2)) for i in range(12)]
        def get(endpoint,**kwargs):
            calls.append(endpoint)
            path=urlsplit(endpoint).path
            if path=='user':v={'login':'example'}
            elif path=='user/repos':v=repositories
            elif path=='notifications':v=[]
            elif path=='search/issues':v={'items':[],'total_count':0}
            elif path.endswith('/actions/runs'):v={'workflow_runs':[{'id':1,'conclusion':'success'}]}
            elif path.endswith('/pulls') or path.endswith('/issues'):v=[]
            else:self.fail(endpoint)
            return json.dumps(v),False
        client.get.side_effect=get
        with tempfile.TemporaryDirectory() as tmp:
            first=GitHubProfile(policy(),Path(tmp),lambda auth:client).overview()
            second=GitHubProfile(policy(),Path(tmp),lambda auth:client).overview()
            self.assertEqual(first['coverage']['discovered_repositories'],12)
            self.assertEqual(first['coverage']['detailed_checks'],10)
            seen={r['repository'] for r in first['repository_checks']+second['repository_checks']}
            self.assertEqual(len(seen),12)
            self.assertEqual(second['repository_checks'][0]['repository'],'example/project10')
            self.assertFalse(first['discovery_limited'])

    def test_shared_tools_enable_profile_without_local_clones(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);c={'github_profile':dict(policy(),enabled=True)}
            tools=shared_tools(home,c,Documents(home,'owner'))
            self.assertIn('github.profile_overview',tools.tools)
            self.assertIn('github.issues',tools.tools)
            self.assertEqual(tools.tools['github.pull_request'].arguments['properties']['repository'],{'type':'string'})
            self.assertTrue(all(not t.mutates for n,t in tools.tools.items() if n.startswith('github.')))
