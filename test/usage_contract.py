"""Projection corruption/backlog, UTC filters and isolation from the web path."""
from datetime import datetime, timezone
import json
import secrets
import sqlite3
import time


def run_usage_contract(t):
    t.reset_rate()
    while t.sql('SELECT count(*) FROM outbox WHERE delivered=0')[0][0]:
        t.run('worker-once')
    start=int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp())
    events=[]
    for offset,complete in [(-1,True),(0,True),(86399,True),(86400,True),(1,False)]:
        events.append(dict(id=secrets.token_hex(16),kind='download',package_ref=t.ref,
            version='22.0.0',variant='source',size=42,complete=complete,created_at=start+offset))
    malformed=['{bad json','null','[]','{}',json.dumps(dict(events[0],id=secrets.token_hex(16),size='bad'))]
    (t.data/'spool/batch').write_text('\n'.join(malformed+[json.dumps(e) for e in events])+'\n')
    t.check(t.invoke('worker-once').returncode==0,'malformed spool records do not poison the batch')
    t.check(not (t.data/'spool/batch').exists() and (t.data/'spool/gap').exists(),'malformed batch acknowledged with explicit coverage gap')
    url=t.base+'/stats/downloads?from=2026-01-01&to=2026-01-02&version=22.0.0&variant=source'
    result=t.request('GET',url)
    t.check(result[0]==200 and result[2]['rows']==[dict(day='2026-01-01',count=2,bytes=84,releases=0,artifacts=0)],'UTC interval is half open and excludes incomplete transfers')
    t.error(t.request('GET',t.base+'/stats/downloads?version=99.99.99'),404,'NOT_FOUND','statistics validates version filter')
    t.error(t.request('GET',t.base+'/stats/downloads?version=22.0.0&variant=missing'),404,'NOT_FOUND','statistics validates variant filter')
    for variant in [None,'source']:
        fields=dict(package_ref=t.ref,version='22.0.0',enabled=True)
        if variant: fields['variant']=variant
        t.op('quarantine',**fields)
        try: t.error(t.request('GET',url),423,'QUARANTINED','statistics preserves quarantine policy')
        finally: t.op('quarantine',**dict(fields,enabled=False))
    # More than one projection batch must not advertise a complete watermark.
    now=int(time.time())
    ids=[secrets.token_hex(16) for _ in range(205)]
    with sqlite3.connect(t.data/'metadata.sqlite') as db:
        db.executemany('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)',[(id,'artifact_published',None,t.ref,'22.0.0','source',42,now-10) for id in ids])
        db.executemany('INSERT INTO outbox VALUES(?,0)',[(id,) for id in ids])
    try:
        t.run('worker-once')
        t.check(t.sql('SELECT count(*) FROM outbox WHERE delivered=0')[0][0]==105,'outbox projection has a bounded batch')
        t.check(t.sql('SELECT at FROM watermark',usage=True)[0][0]<now-10,'watermark stays behind pending durable events')
        t.run('worker-once'); t.run('worker-once')
        t.check(t.sql('SELECT count(*) FROM outbox WHERE delivered=0')[0][0]==0 and t.sql('SELECT at FROM watermark',usage=True)[0][0]>=now-1,'watermark advances after backlog drains')
    finally:
        with sqlite3.connect(t.data/'metadata.sqlite') as db:
            db.executemany('DELETE FROM outbox WHERE id=?',[(id,) for id in ids])
            db.executemany('DELETE FROM audit WHERE id=?',[(id,) for id in ids])
        with sqlite3.connect(t.data/'usage.sqlite') as db:
            db.executemany('DELETE FROM events WHERE id=?',[(id,) for id in ids])
    # A projection failure leaves the batch retryable and the public path alive.
    (t.data/'spool/batch').write_text(json.dumps(dict(events[0],id=secrets.token_hex(16)))+'\n')
    with sqlite3.connect(t.data/'usage.sqlite') as db:
        db.execute('BEGIN IMMEDIATE')
        failed=t.invoke('worker-once')
        t.check(failed.returncode!=0 and (t.data/'spool/batch').exists(),'locked usage projection keeps unacknowledged batch')
        t.check(t.request('GET',t.base+'/index')[0]==200,'projection failure leaves metadata available')
    t.run('worker-once')
    t.check(not (t.data/'spool/batch').exists(),'projection recovers after database lock release')
