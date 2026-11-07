"""Private request lifecycle telemetry, independent of transcript artifacts."""
import hashlib
import sqlite3
import time
from contextlib import contextmanager,closing
from pathlib import Path

PHASES={'background','message','received','acknowledged','execution_started','provider_started','provider_finished',
        'tool_started','tool_finished','completed','first_reply','delivered'}


class RequestTiming:
    def __init__(self,home,event_id):
        self.path=Path(home)/'request-timing.sqlite3'
        self.key=hashlib.sha256(str(event_id).encode()).hexdigest()

    def mark(self,phase,*,at=None,label=''):
        if phase not in PHASES:raise ValueError('Unknown lifecycle phase')
        # Catalog names and fixed statuses only; never arbitrary provider payloads.
        if not isinstance(label,str) or len(label)>80 or any(not (c.isalnum() or c in '._-') for c in label):raise ValueError('Invalid timing label')
        at=time.time() if at is None else at
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS lifecycle(seq INTEGER PRIMARY KEY,request TEXT,phase TEXT,at REAL,label TEXT)')
            if phase not in ('provider_started','provider_finished','tool_started','tool_finished'):
                if db.execute('SELECT 1 FROM lifecycle WHERE request=? AND phase=?',(self.key,phase)).fetchone():return
            db.execute('INSERT INTO lifecycle(request,phase,at,label) VALUES (?,?,?,?)',(self.key,phase,at,label))
        self.path.chmod(0o600)

    @contextmanager
    def activity(self,kind,label):
        self.mark(kind+'_started',label=label)
        try:yield
        finally:self.mark(kind+'_finished',label=label)

    def read(self):
        if not self.path.exists():return []
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            return [{'phase':p,'at':a,'label':l} for p,a,l in db.execute('SELECT phase,at,label FROM lifecycle WHERE request=? ORDER BY seq',(self.key,))]


def summary(home,days=7,now=None,request_keys=None):
    """Aggregate observed wall times without exposing request identifiers."""
    if type(days) is not int or not 1<=days<=90:raise ValueError('Choose 1–90 days')
    path=Path(home)/'request-timing.sqlite3'
    if not path.exists():return {'requests':0,'metrics':{},'coverage':'No lifecycle measurements yet.'}
    now=time.time() if now is None else now
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT request,phase,MIN(at) FROM lifecycle GROUP BY request,phase').fetchall()
    grouped={}
    for key,phase,at in rows:grouped.setdefault(key,{})[phase]=at
    selected=[r for key,r in grouped.items() if (request_keys is None or key in request_keys) and now-days*86400<=r.get('received',0)<=now]
    metrics={}
    for name,first,last in (('delivery_to_host','message','received'),('acknowledgement','received','acknowledged'),
                            ('queue','received','execution_started'),('first_response','received','first_reply'),
                            ('execution','execution_started','completed'),('delivery','completed','delivered'),('end_to_end','received','delivered')):
        values=sorted(r[last]-r[first] for r in selected if first in r and last in r and r[last]>=r[first])
        if values:
            import statistics
            metrics[name]={'count':len(values),'median_seconds':round(statistics.median(values),3),
                           'p90_seconds':round(values[int((len(values)-1)*.9)],3),'max_seconds':round(max(values),3)}
    return {'requests':len(selected),'metrics':metrics,
            'coverage':'Observed wall times only, including host suspension. Delivery-to-host is separate from queue/execution. Acknowledgement is best effort; partial timelines are omitted per metric. Historical requests are not reconstructed. No latency guarantee is implied.'}


if __name__=='__main__':
    import argparse,json,os
    parser=argparse.ArgumentParser(description='Summarize private request lifecycle wall times')
    parser.add_argument('--home',type=Path,default=Path(os.environ.get('CAPO_HOME',str(Path.home()/'.local/share/capo'))))
    parser.add_argument('--days',type=int,default=7)
    args=parser.parse_args();print(json.dumps(summary(args.home,args.days)))
