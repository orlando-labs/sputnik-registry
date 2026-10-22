"""Quota edges using persisted counters instead of multi-gigabyte allocations."""
import secrets
import sqlite3
import time


def clone(t, table, where, replacements):
    with sqlite3.connect(t.data/'metadata.sqlite') as db:
        db.row_factory=sqlite3.Row
        row=dict(db.execute('SELECT * FROM '+table+' WHERE '+where).fetchone())
        row.update(replacements)
        db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))


def run_quota_contract(t):
    t.reset_rate(); payload=t.artifact('29.0.0')
    account=t.ctx['account']['account_id']; other=t.ctx['other']['account_id']
    for name,compressed,pending,global_pending,storage in [
        ('standard',67108864,201326592,536870912,10737418240),
        ('native-large',2147483648,4294967296,8589934592,107374182400)]:
        t.restart(native_large=name=='native-large')
        t.error(t.create(payload,'29.0.0',limit_profile=name,expected_size=compressed+1),400,'INVALID_SIZE','compressed admission limit '+name)
        edge=t.create(payload,'29.0.0',limit_profile=name,expected_size=compressed)
        t.check(edge[0]==201,'compressed admission boundary '+name); t.cancel(edge[2]['id'])
        seed=t.create(payload,'29.0.1',limit_profile=name)[2]['id']
        try:
            for owner,limit,label in [(account,pending,'account'),(other,global_pending,'global')]:
                t.sql('UPDATE sessions SET size=?,account_id=? WHERE id=?',(limit-len(payload),owner,seed))
                edge=t.create(payload,'29.0.2',limit_profile=name)
                t.check(edge[0]==201,label+' pending boundary admits '+name); t.cancel(edge[2]['id'])
                t.sql('UPDATE sessions SET size=size+1 WHERE id=?',(seed,))
                t.error(t.create(payload,'29.0.2',limit_profile=name),429,'PENDING_QUOTA',label+' pending overflow '+name)
        finally:
            t.sql('UPDATE sessions SET size=?,account_id=? WHERE id=?',(len(payload),account,seed)); t.cancel(seed)
        # Reserve logical storage across variants and owners, independent of upload size.
        row=t.sql("SELECT size,profile FROM artifacts WHERE ref=? AND version='22.0.0' AND variant='source'",(t.ref,))[0]
        others=t.sql("SELECT coalesce(sum(a.size),0) FROM artifacts a JOIN packages p ON p.ref=a.ref WHERE p.quota_owner='namespace:test' AND a.profile=? AND NOT (a.ref=? AND a.version='22.0.0' AND a.variant='source')",(name,t.ref))[0][0]
        try:
            t.sql("UPDATE artifacts SET size=?,profile=? WHERE ref=? AND version='22.0.0' AND variant='source'",(storage-others-len(payload),name,t.ref))
            edge=t.create(payload,'29.0.3',limit_profile=name); t.check(edge[0]==201,'logical storage boundary '+name); t.cancel(edge[2]['id'])
            t.sql("UPDATE artifacts SET size=size+1 WHERE ref=? AND version='22.0.0' AND variant='source'",(t.ref,))
            t.error(t.create(payload,'29.0.3',limit_profile=name),429,'STORAGE_QUOTA','logical storage overflow '+name)
        finally: t.sql("UPDATE artifacts SET size=?,profile=? WHERE ref=? AND version='22.0.0' AND variant='source'",(*row,t.ref))
        t.reset_rate()
    t.restart(native_large=False)
    seed=t.create(payload,'29.1.0')[2]['id']; ids=[]
    try:
        for i in range(63):
            id=secrets.token_hex(16); ids.append(id)
            clone(t,'sessions',"id='"+seed+"'",dict(id=id,account_id=other,version='29.1.'+str(i+1)))
        t.error(t.create(payload,'29.2.0'),429,'SESSION_LIMIT','64 global sessions block a new reservation')
        t.sql('DELETE FROM sessions WHERE id=?',(ids.pop(),))
        edge=t.create(payload,'29.2.0'); t.check(edge[0]==201,'63 global sessions admit the 64th'); t.cancel(edge[2]['id'])
        for id in ids[:2]: t.sql('UPDATE sessions SET receiving_until=? WHERE id=?',(int(time.time())+120,id))
        t.error(t.upload(seed,payload),503,'UPLOAD_CAPACITY','global concurrent receiving cap spans accounts')
    finally:
        for id in ids: t.sql('DELETE FROM sessions WHERE id=?',(id,))
        t.cancel(seed)
    try:
        for i in range(31):
            clone(t,'artifacts',"ref='"+t.ref+"' AND version='22.0.0' AND variant='source'",dict(variant='boundary-'+str(i)))
        t.error(t.create(payload,'22.0.0',variant='new'),429,'VARIANT_LIMIT','33rd variant rejected across published and reserved')
        t.sql("DELETE FROM artifacts WHERE ref=? AND version='22.0.0' AND variant='boundary-30'",(t.ref,))
        edge=t.create(payload,'22.0.0',variant='new'); t.check(edge[0]==201,'32nd variant admitted')
        t.error(t.create(payload,'22.0.0',variant='another'),429,'VARIANT_LIMIT','reserved variant consumes release slot'); t.cancel(edge[2]['id'])
    finally: t.sql("DELETE FROM artifacts WHERE ref=? AND version='22.0.0' AND variant LIKE 'boundary-%'",(t.ref,))
    t.reset_rate()
