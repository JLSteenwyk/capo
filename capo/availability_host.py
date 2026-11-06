"""Optional private AC-power policy and external dead-man heartbeat publisher."""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .conversation import _write
from .web_tools import public_url, PinnedHTTPS


def settings(value):
    if not isinstance(value, dict) or set(value)-{'prevent_ac_sleep','heartbeat_url','interval','max_health_age'}:
        raise ValueError('Unsupported availability setting')
    result={'prevent_ac_sleep':False,'heartbeat_url':'','interval':60,'max_health_age':30,**value}
    if type(result['prevent_ac_sleep']) is not bool:raise ValueError('prevent_ac_sleep must be boolean')
    for key,low,high in (('interval',15,300),('max_health_age',5,120)):
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError('Invalid '+key)
    if not isinstance(result['heartbeat_url'],str):raise ValueError('heartbeat_url must be a string')
    if result['heartbeat_url']:
        from .web_tools import WebError
        try:public_url(result['heartbeat_url'])
        except WebError:raise ValueError('heartbeat_url must be public HTTPS') from None
    return result


def eligible(value, now, max_age):
    at=value.get('at')
    return (type(at) in (int,float) and 0<=now-at<=max_age
            and value.get('connected') is True
            and (value.get('cycle_completed') is True or value.get('probation') is True or value.get('drained') is True))


def ping(url):
    p=public_url(url)
    connection=PinnedHTTPS(p.hostname,timeout=10)
    try:
        connection.request('GET',p.path+('?' + p.query if p.query else '') or '/')
        response=connection.getresponse()
        if not 200<=response.status<300:raise RuntimeError('Monitor rejected heartbeat')
    finally:connection.close()


class AvailabilityHost:
    def __init__(self, home, config):
        self.home=Path(home);self.config=settings(config);self.child=None
        self.stop=threading.Event()

    def power(self):
        if not self.config['prevent_ac_sleep']:return
        if sys.platform!='darwin':raise RuntimeError('AC sleep prevention requires macOS')
        if self.child is None or self.child.poll() is not None:
            self.child=subprocess.Popen(['/usr/bin/caffeinate','-s','-w',str(os.getpid())],
                                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def step(self, now=None):
        self.power()
        now=time.time() if now is None else now
        try:value=json.loads((self.home/'updates/health.json').read_text())
        except (OSError,ValueError):value={}
        healthy=eligible(value,now,self.config['max_health_age'])
        status='unconfigured' if not self.config['heartbeat_url'] else 'withheld'
        if healthy and self.config['heartbeat_url']:
            try:ping(self.config['heartbeat_url']);status='accepted'
            except Exception:status='failed'
        _write(self.home/'availability.json',{'checked_at':now,'healthy':healthy,
            'ac_sleep_policy':self.config['prevent_ac_sleep'],'external_heartbeat':status})
        return status

    def close(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=5)

    def run(self):
        try:
            while not self.stop.is_set():
                self.step()
                self.stop.wait(self.config['interval'])
        finally:self.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--home',type=Path,default=Path(os.environ.get('CAPO_HOME',str(Path.home()/'.local/share/capo'))))
    args=parser.parse_args()
    host=AvailabilityHost(args.home,json.loads(args.config.read_text()))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:host.stop.set())
    host.run()


if __name__=='__main__':main()
