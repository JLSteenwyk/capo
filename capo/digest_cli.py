"""Private digest setup and preview commands; no credentials in command output."""
import json
import os
import shlex
import time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

from .digest import DigestStore,next_delivery,scope


def load_slack_environment(path):
    if path.stat().st_mode & 0o077:
        raise ValueError('The Slack environment file must have owner-only permissions')
    values={}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        tokens=shlex.split(line,comments=True)
        if tokens and tokens[0]=='export':tokens=tokens[1:]
        if len(tokens)!=1 or '=' not in tokens[0]:raise ValueError('Invalid private environment file')
        key,value=tokens[0].split('=',1)
        if key in ('SLACK_APP_TOKEN','SLACK_BOT_TOKEN'):values[key]=value
    if not all(values.get(k) for k in ('SLACK_APP_TOKEN','SLACK_BOT_TOKEN')):
        raise ValueError('Slack tokens are missing from the private environment file')
    os.environ.update(values)


def command(args):
    from .slack import validate_config
    config=validate_config(json.loads(args.config.read_text()))
    db=DigestStore(args.home);owner=scope(config)
    try:
        if args.command=='digest-settings':
            changes={}
            if args.enable:changes['enabled']=True
            if args.pause:changes['enabled']=False
            if args.time:changes['time']=args.time
            if args.artists_file:changes['artists']=json.loads(args.artists_file.read_text())['artists']
            p=db.configure(owner,changes) if changes else db.preferences(owner)
            print(json.dumps(p,indent=2))
            print('Next scheduled time:',next_delivery(datetime.now(timezone.utc),p).isoformat() if p['enabled'] else 'paused')
            return 0
        from .digest_service import DigestManager
        from .store import Store
        from slack_sdk import WebClient
        load_slack_environment(args.env_file)
        client=WebClient(token=os.environ['SLACK_BOT_TOKEN'])
        auth=client.auth_test()
        if auth['team_id']!=config['team_id']:raise ValueError('Slack workspace mismatch')
        store=Store(args.home)
        manager=DigestManager(SimpleNamespace(store=store,config=config,client=client,bot_user_id=auth['user_id']))
        try:
            key=owner+':preview:'+args.id
            for _ in range(600):
                run=manager.preview(key)
                if run['status']=='sent':
                    print('Digest preview delivered to the configured Slack channel.')
                    return 0
                if run['status']=='sending':manager.deliver(run,datetime.now(timezone.utc))
                if run['status'] in ('failed','expired'):raise ValueError('Preview failed; inspect private digest records')
                time.sleep(1)
            raise ValueError('Preview is still pending; rerun with the same preview ID to check it')
        finally:
            manager.db.close();store.db.close()
    finally:db.close()
