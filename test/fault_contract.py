"""Real worker kills, syscall errors, hostile archives and transport failures."""
from concurrent.futures import ThreadPoolExecutor
import errno
import gzip
import hashlib
import io
import json
import os
import platform
import secrets
import signal
import socket
import sqlite3
import struct
import subprocess
import tarfile
import time


def fault_environment(t, library, op, match, error=None):
    marker=t.work/('fault-'+secrets.token_hex(8))
    env=dict(t.env,REGISTRY_TEST_OP=op,REGISTRY_TEST_ROOT=str(t.data),REGISTRY_TEST_MATCH=match,REGISTRY_TEST_MARKER=str(marker))
    env['DYLD_INSERT_LIBRARIES' if platform.system()=='Darwin' else 'LD_PRELOAD']=str(library)
    if error is not None: env['REGISTRY_TEST_ERRNO']=str(error)
    return env,marker


def worker_faults(t, library):
    t.reset_rate()
    for index,(op,match) in enumerate([('fsync','/staging/'),('linkat','/blobs/'),('fsync','/blobs/'),('unlinkat','/staging/')]):
        version='25.0.'+str(index); payload=t.artifact(version); sid=t.queue(payload,version)
        env,marker=fault_environment(t,library,op,match)
        process=subprocess.Popen(t.command+['--config',str(t.config),'worker-once'],cwd=t.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            t.wait(lambda:marker.exists() or process.poll() is not None,'fault reached',timeout=120)
            if not marker.exists():
                out,err=process.communicate(timeout=10)
                raise AssertionError('worker missed '+op+' '+match+': '+str(process.returncode)+' '+out.decode()[-1000:]+err.decode()[-1000:])
            t.check(True,'worker reaches '+op+' '+match)
            process.kill(); process.communicate(timeout=10)
            t.sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,sid))
            t.run('worker-once'); t.run('worker-once')
            t.check(t.session(sid)['state']=='published','worker recovers kill after '+op+' '+match)
            t.check(t.sql("SELECT count(*) FROM audit WHERE ref=? AND version=? AND kind='artifact_published'",(t.ref,version))[0][0]==1,'crash recovery publishes exactly once')
            url=t.base+'/versions/'+version+'/artifacts/source/download'
            status,_,body=t.request('GET',url,raw=True)
            t.check(status==200 and body==payload,'crash recovery preserves downloadable bytes')
        finally:
            if process.poll() is None: process.kill(); process.communicate(timeout=10)
    for index,(op,match,error) in enumerate([('fsync','/staging/',errno.EIO),('linkat','/blobs/',errno.ENOSPC),('fsync','/blobs/',errno.EIO)]):
        version='25.1.'+str(index); payload=t.artifact(version); sid=t.queue(payload,version)
        env,marker=fault_environment(t,library,op,match,error)
        result=t.invoke('worker-once',env=env)
        t.check(marker.exists(),'injected '+op+' errno '+str(error))
        row=t.sql('SELECT state,reserved,reason_code FROM sessions WHERE id=?',(sid,))[0]
        t.check(row[0]=='promoting' and row[1]==1 and row[2]=='RETRY_PENDING','IO failure retains durable job and quota: '+str(row))
        t.check(not t.sql('SELECT 1 FROM artifacts WHERE ref=? AND version=?',(t.ref,version)),'IO failure has no visible artifact')
        t.sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,sid))
        t.run('worker-once')
        state=t.session(sid)
        t.check(state['state']=='published' and 'reason_code' not in state,'transient IO error recovers without stale error')
    # Fencing: an active lease is never stolen, even when the command restarts.
    payload=t.artifact('25.2.0'); sid=t.queue(payload,'25.2.0')
    t.sql('UPDATE sessions SET lease_until=?,worker_fence=? WHERE id=?',(int(time.time())+600,secrets.token_hex(16),sid))
    t.check(t.run('worker-once')['processed'] is False,'unexpired worker lease is exclusive')
    t.sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,sid)); t.run('worker-once')
    t.check(t.session(sid)['state']=='published','expired lease is reclaimed')


def fences_and_revocation(t,library):
    account=t.ctx['account']['account_id']
    for i,mode in enumerate(['fence','acl','native-grant']):
        t.reset_rate(); version='25.3.'+str(i)
        ref='test/provider_none' if mode=='native-grant' else t.ref
        if mode=='native-grant':
            name,body,target,provider=t.library()
            payload=t.artifact(version,ref=ref,extra={**target, **provider},files={'main.s':'package test.contract\nexport answer\ndef answer(): 42\n',name:body})
        else: payload=t.artifact(version)
        sid=t.queue(payload,version,ref=ref)
        env,marker=fault_environment(t,library,'linkat','/blobs/')
        p=subprocess.Popen(t.command+['--config',str(t.config),'worker-once'],cwd=t.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            t.wait(marker.exists,'worker paused before final transaction',timeout=120)
            if mode=='fence': t.sql('UPDATE sessions SET worker_fence=? WHERE id=?',(secrets.token_hex(16),sid))
            if mode=='acl': t.sql('DELETE FROM owners WHERE ref=? AND account_id=?',(ref,account))
            if mode=='native-grant': t.sql("DELETE FROM grants WHERE account_id=? AND kind='native' AND target=?",(account,ref))
            t.check(t.request('GET',t.base+'/index')[0]==200,'web remains available while worker is stopped')
            p.send_signal(signal.SIGCONT); p.communicate(timeout=120)
            t.check(not t.sql('SELECT 1 FROM artifacts WHERE ref=? AND version=?',(ref,version)),'final transaction rechecks '+mode)
            if mode=='fence':
                t.check(t.sql('SELECT reserved FROM sessions WHERE id=?',(sid,))[0][0]==1,'stale worker cannot refund current reservation')
                t.sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,sid)); t.run('worker-once')
                t.check(t.session(sid)['state']=='published','new fence recovers safely')
            else:
                t.check(t.sql('SELECT state,reserved FROM sessions WHERE id=?',(sid,))[0]==('rejected',0),'revoked permission rejects and refunds once')
        finally:
            if p.poll() is None: p.kill(); p.communicate(timeout=10)
            if mode=='acl': t.sql("INSERT OR REPLACE INTO owners VALUES(?,?,'owner')",(ref,account))
            if mode=='native-grant': t.op('grant',account_id=account,kind='native',target=ref)
    # Repeated abrupt termination follows the same recovery path as an externally
    # killed timeout/OOM worker, without exhausting the test host's memory.
    payload=t.artifact('25.4.0'); sid=t.queue(payload,'25.4.0')
    for _ in range(2):
        env,marker=fault_environment(t,library,'fsync','/staging/')
        p=subprocess.Popen(t.command+['--config',str(t.config),'worker-once'],cwd=t.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            t.wait(marker.exists,'repeated interruption point',timeout=120)
            p.kill(); p.communicate(timeout=10)
        finally:
            if p.poll() is None: p.kill(); p.communicate(timeout=10)
        t.check(t.sql('SELECT reserved FROM sessions WHERE id=?',(sid,))[0][0]==1,'abrupt worker death retains credit')
        t.sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,sid))
    t.run('worker-once')
    t.check(t.session(sid)['state']=='published','repeated worker death recovers')


def archives(t):
    t.reset_rate()
    for index,mode in enumerate(['traversal','symlink','hardlink','duplicate','checksum','truncated','bad-bytecode']):
        version='26.0.'+str(index); original=t.artifact(version)
        with tarfile.open(fileobj=io.BytesIO(original)) as archive:
            members=[(m,archive.extractfile(m).read()) for m in archive.getmembers()]
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for item,body in members:
                if mode=='checksum' and item.name=='checksums.sha256': body=b'0'+body[1:]
                item.size=len(body); archive.addfile(item,io.BytesIO(body))
            item=tarfile.TarInfo('../outside' if mode=='traversal' else 'payload')
            if mode in ['symlink','hardlink']:
                item.type=tarfile.SYMTYPE if mode=='symlink' else tarfile.LNKTYPE; item.linkname='../outside'; archive.addfile(item)
            if mode=='traversal': item.size=1; archive.addfile(item,io.BytesIO(b'x'))
            if mode=='duplicate':
                item,body=members[0]; archive.addfile(item,io.BytesIO(body))
            if mode=='bad-bytecode':
                item.name='bad.sputnikbc'; item.size=16; archive.addfile(item,io.BytesIO(b'bad bytecode!!!!'))
        payload=stream.getvalue()
        if mode=='truncated': payload=payload[:len(payload)//2]
        state=t.publish(payload,version)
        t.check(state['state']=='rejected','hostile archive rejected '+mode)
        retry=t.create(payload,version); t.check(retry[0]==201,'hostile archive frees reservation '+mode); t.cancel(retry[2]['id'])

    for i,(profile,limit) in enumerate([('standard',67108864),('native-large',4294967296)]):
        version='26.1.'+str(i)
        original=t.artifact(version,fields={'limit_profile': profile})
        prefix=io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(original)) as archive:
            for name in ['sputnik.build.json','checksums.sha256']:
                member=archive.getmember(name); body=archive.extractfile(member).read()
                prefix.write(member.tobuf(format=tarfile.USTAR_FORMAT)); prefix.write(body); prefix.write(b'\0'*((-len(body))%512))
        member=tarfile.TarInfo('too-large.bin'); member.size=limit+1
        # An oversized declared member is rejected before any of its bytes exist.
        payload=gzip.compress(prefix.getvalue()+member.tobuf(format=tarfile.USTAR_FORMAT))
        path=t.work/('member-limit-'+profile+'.sputnikpkg'); path.write_bytes(payload)
        result=subprocess.run([str(t.ctx['SDK']),'pkg','inspect',str(path)],capture_output=True,text=True)
        t.check(result.returncode!=0 and 'member exceeds size budget' in result.stderr,'inspector enforces member header bound before reading '+profile)
        t.restart(native_large=profile=='native-large')
        t.check(t.publish(payload,version,limit_profile=profile)['state']=='rejected','registry enforces member bound '+profile)
    t.restart(native_large=False)
    version='26.2.0'; original=t.artifact(version)
    stream=io.BytesIO()
    with gzip.GzipFile(fileobj=stream,mode='wb') as zipped:
        with tarfile.open(fileobj=io.BytesIO(original)) as archive:
            for name in ['sputnik.build.json','checksums.sha256']:
                member=archive.getmember(name); body=archive.extractfile(member).read()
                zipped.write(member.tobuf(format=tarfile.USTAR_FORMAT)); zipped.write(body); zipped.write(b'\0'*((-len(body))%512))
        block=b'\0'*(1024*1024)
        for i in range(4):
            member=tarfile.TarInfo('expanded-'+str(i)); member.size=67108864
            zipped.write(member.tobuf(format=tarfile.USTAR_FORMAT))
            for _ in range(64): zipped.write(block)
        zipped.write(b'\0'*1024)
    payload=stream.getvalue(); path=t.work/'expanded-limit.sputnikpkg'; path.write_bytes(payload)
    result=subprocess.run([str(t.ctx['SDK']),'pkg','inspect',str(path)],capture_output=True,text=True)
    t.check(result.returncode!=0 and 'total member budget exceeded' in result.stderr,'inspector rejects compressed expansion over standard budget')
    t.check(t.publish(payload,version)['state']=='rejected','registry enforces expanded standard budget')


def downloads(t):
    t.reset_rate()
    data=os.urandom(4*1024*1024)
    payload=t.artifact('27.0.0',extra={'files': {'include': ['payload.bin']}},files={'main.s':'package test.contract\nexport answer\ndef answer(): 42\n','payload.bin':data})
    t.check(t.publish(payload,'27.0.0')['state']=='published','large download fixture')
    url=t.base+'/versions/27.0.0/artifacts/source/download'
    t.run('worker-once')
    before=t.sql("SELECT count(*) FROM events WHERE kind='download' AND version='27.0.0' AND complete=1",usage=True)[0][0]
    sock=socket.socket(); sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024); sock.settimeout(30); sock.connect(('127.0.0.1',t.port))
    sock.sendall(('GET '+url+' HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer '+t.token+'\r\n\r\n').encode())
    received=sock.recv(1024); t.check(b'200' in received,'abort begins actual transfer')
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0)); sock.close()
    def projected_abort():
        t.run('worker-once')
        return t.sql("SELECT size,complete FROM events WHERE kind='download' AND version='27.0.0'",usage=True)
    events=t.wait(projected_abort,'terminal abort recorded',timeout=60)
    t.check(any(not complete and size<len(payload) for size,complete in events),'RST records short unsuccessful transport')
    t.check(t.sql("SELECT count(*) FROM events WHERE kind='download' AND version='27.0.0' AND complete=1",usage=True)[0][0]==before,'RST never counts as full download')
    result=t.request('GET',url,headers={'Range':'bytes=0-9'},raw=True)
    t.check(result[0]==200 and result[2]==payload,'Range returns full verified 200')
    t.run('worker-once')
    t.check(t.sql("SELECT count(*) FROM events WHERE kind='download' AND version='27.0.0' AND complete=1",usage=True)[0][0]==before+1,'full Range fallback counts once')


def snapshot_concurrency(t):
    t.reset_rate()
    payload=t.artifact('28.0.0'); sid=t.queue(payload,'28.0.0')
    backup=t.work/'concurrent-backup'
    # Keep a WAL reader alive while a separate worker commits and backup snapshots.
    with sqlite3.connect(t.data/'metadata.sqlite') as reader:
        reader.execute('BEGIN'); reader.execute('SELECT count(*) FROM artifacts').fetchone()
        with ThreadPoolExecutor(max_workers=2) as pool:
            worker=pool.submit(t.run,'worker-once')
            snapshot=pool.submit(t.invoke,'backup',['--out',str(backup)])
            worker.result(); result=snapshot.result()
        t.check(result.returncode==0,'backup overlaps publication with live WAL reader')
    settings=json.loads(t.config.read_text()); settings['data_dir']=str(t.work/'concurrent-restored')
    config=t.work/'concurrent-restored.json'; config.write_text(json.dumps(settings))
    t.check(t.invoke('restore',['--backup',str(backup)],config=config).returncode==0,'concurrent backup restores')
    with sqlite3.connect(settings['data_dir']+'/metadata.sqlite') as db:
        for digest,size in db.execute('SELECT digest,size FROM artifacts'):
            path=t.work/'concurrent-restored/blobs'/digest[:2]/digest
            t.check(path.stat().st_size==size and hashlib.sha256(path.read_bytes()).hexdigest()==digest,'snapshot references a durable exact blob')
        digest=hashlib.sha256(t.token.encode()).hexdigest()
        t.check(db.execute('SELECT count(*) FROM tokens WHERE digest=?',(digest,)).fetchone()[0]==1,'snapshot retains credentials')


def spool_failures(t,library):
    t.reset_rate()
    event=dict(id=secrets.token_hex(16),kind='download',package_ref=t.ref,version='22.0.0',variant='source',size=42,complete=True,created_at=int(time.time()))
    t.run('worker-once')
    (t.data/'spool/events').write_text(json.dumps(event)+'\n')
    env,marker=fault_environment(t,library,'renameat','/spool/batch',errno.EIO)
    result=t.invoke('worker-once',env=env)
    t.check(marker.exists() and result.returncode!=0,'spool rotation error is observable')
    t.check((t.data/'spool/events').exists(),'spool rotation failure retains unread events')
    t.check(t.request('GET',t.base+'/index')[0]==200,'spool rotation error leaves web available')
    t.run('worker-once')
    t.check(t.sql('SELECT count(*) FROM events WHERE id=?',(event['id'],),usage=True)[0][0]==1,'spool rotation retry delivers once')
    # Inject into the web process's completion observer, after successful HTTP.
    env,marker=fault_environment(t,library,'fsync','/spool/events',errno.EIO)
    original=dict(t.env)
    try:
        t.env.update(env); t.restart()
        t.env.clear(); t.env.update(original)
        result=t.request('GET',t.base+'/versions/22.0.0/artifacts/source/download',raw=True)
        t.check(result[0]==200,'observer error does not change successful transfer')
        t.wait(marker.exists,'download observer fault reached')
        t.wait(lambda:(t.data/'spool/gap').exists(),'observer marks uncertain durability')
        t.check(t.request('GET',t.base+'/index')[0]==200,'web survives observer IO failure')
    finally:
        t.env.clear(); t.env.update(original); t.restart()
    t.run('worker-once')


def run_fault_contract(t):
    library=t.work/('faults.dylib' if platform.system()=='Darwin' else 'faults.so')
    flags=['-dynamiclib'] if platform.system()=='Darwin' else ['-shared','-fPIC','-ldl']
    build=subprocess.run(['clang','-std=c11']+flags+[str(t.root/'test/faults.c'),'-o',str(library)],capture_output=True,text=True)
    t.check(build.returncode==0,'compile isolated syscall fault fixture: '+build.stderr[-200:])
    worker_faults(t,library)
    fences_and_revocation(t,library)
    archives(t)
    downloads(t)
    spool_failures(t,library)
    snapshot_concurrency(t)
    # Counter fixtures deliberately follow snapshot validation, and are cleaned
    # before the final projection/coverage checks.
    from quota_contract import run_quota_contract
    from usage_contract import run_usage_contract
    run_quota_contract(t)
    run_usage_contract(t)
