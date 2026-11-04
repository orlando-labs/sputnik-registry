#!/usr/bin/env python3
"""Loopback contract/fault tests against real Sputnik web/worker processes."""
from manifest import write_source_manifest
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from regressions import run_regressions
from conformance import run_conformance

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(os.environ.get('SPUTNIK',str(ROOT.parent/'sputnik-lang/build/sputnik'))).resolve()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--vm', action='store_true')
    p.add_argument('--auth-only', action='store_true')
    a = p.parse_args()
    command = [str(SDK),'run',str(ROOT/'.build/registry.build.json'),'--grant','ffi','--grant','fs.read','--grant','fs.write','--grant','fs.metadata','--grant','random.secure','--grant','net.listen','--grant','net.connect','--grant','process.spawn','--grant','process.signal','--'] if a.vm else [str(ROOT/'.build/native/registry')]
    runtime_sha256 = hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()
    work = Path(tempfile.mkdtemp(prefix='integration-',dir=ROOT/'.build'))
    checks = []
    # Operational subprocesses must work with no interpreters or helper
    # commands on PATH. The SDK compiler is used only for fixture creation.
    runtime_env = dict(os.environ)
    if not a.vm:
        runtime_env['PATH']='/nonexistent'
    def check(value, name):
        if not value:
            raise AssertionError(name)
        checks.append(name)
        print('ok:',name,flush=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    config = work/'config.json'
    initialized=subprocess.run(command+['--config',str(config),'init','--data-dir',str(work/'data'),'--migrations-dir',str(ROOT/'migrations'),'--port',str(port)],cwd=ROOT,env=runtime_env,capture_output=True,text=True,timeout=240)
    if initialized.returncode:
        raise RuntimeError(initialized.stderr[-4000:])
    check(config.stat().st_mode & 0o077==0,'Sputnik init creates private config')
    def run(mode, data=None, options=(), selected_config=config):
        result = subprocess.run(command+['--config',str(selected_config),mode]+list(options),input=json.dumps(data) if data is not None else None,cwd=ROOT,env=runtime_env,capture_output=True,text=True,timeout=240)
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:]+result.stdout[-2000:])
        return json.loads(result.stdout)
    def op(command, **data):
        return run('operator',dict(command=command,**data))
    account = run('account-create',options=['--label','Publisher','--namespace','test'])
    other = op('account-create',label='Other',namespace='other')
    token = account['token']
    base = '/api/v1/namespaces/test/packages/demo'
    def request(method,path,data=None,*,auth=token,key=None,headers=None,raw=False):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
        h={'Content-Type':'application/json'}
        if auth:
            h['Authorization']='Bearer '+auth
        if method in ['POST','DELETE']:
            h['Idempotency-Key']=key or secrets.token_hex(16)
        h.update(headers or {})
        body=data if isinstance(data,bytes) else (json.dumps(data).encode() if data is not None else None)
        conn.request(method,path,body,h)
        response=conn.getresponse()
        payload=response.read()
        try:
            parsed = payload if raw or method=='HEAD' or response.status==304 else json.loads(payload)
        except ValueError:
            raise RuntimeError(f'{method} {path}: HTTP {response.status}: {payload!r}')
        result=(response.status,dict(response.getheaders()),parsed)
        conn.close()
        return result
    log=(work/'server.log').open('w')
    server=None
    def start():
        process=subprocess.Popen(command+['--config',str(config),'serve'],cwd=ROOT,env=runtime_env,stdout=log,stderr=log)
        for _ in range(1200 if a.vm else 300):
            if process.poll() is not None:
                raise RuntimeError((work/'server.log').read_text()[-4000:])
            try:
                if request('GET','/health/ready',auth=None)[0]==200:
                    return process
            except (ConnectionError,OSError):
                pass
            time.sleep(.1)
        process.terminate()
        raise TimeoutError('server readiness')
    def fixture(ver='1.0.0',var='source',mode='source',extra=None):
        source=work/(ver+'-'+var)
        source.mkdir()
        (source/'main.s').write_text('package test.demo\nexport answer\ndef answer(): 42\n')
        (source/'helper.s').write_text('package test.helper\nexport answer\ndef answer(): 7\n')
        write_source_manifest(source, 'test/demo', ver, var, {'main.s': 'test.demo', 'helper.s': 'test.helper'}, ['main.s','helper.s'], extra)
        out=work/(ver+'-'+var+'.sputnikpkg')
        args=[str(SDK),'pkg','build',str(source),'--out',str(out),'--variant',var,'--modules',mode]
        if mode=='mixed':args+=['--bytecode-module','test.helper']
        r=subprocess.run(args,capture_output=True,text=True)
        if r.returncode:raise RuntimeError(r.stderr+r.stdout)
        return out.read_bytes()
    def create(payload,ver='1.0.0',var='source',key=None,**overrides):
        data=dict(package_ref='test/demo',version=ver,variant=var,expected_size=len(payload),expected_sha256='sha256:'+hashlib.sha256(payload).hexdigest())
        data.update(overrides)
        return request('POST','/api/v1/publish-sessions',data,key=key)
    def upload(id,payload,auth=token):
        return request('PUT','/api/v1/publish-sessions/'+id+'/artifact',payload,auth=auth,headers={'Content-Type':'application/vnd.sputnik.package.v1'})
    def publish(payload,ver='1.0.0',var='source'):
        status,_,session=create(payload,ver,var)
        check(status==201,'create '+ver+'/'+var)
        id=session['id']
        check(upload(id,payload)[0]==200,'upload '+var)
        check(request('POST','/api/v1/publish-sessions/'+id+'/commit',{})[0]==202,'commit '+var)
        run('worker-once')
        result=request('GET','/api/v1/publish-sessions/'+id)[2]
        check(result['state']=='published','worker published '+var+': '+str(result))
        return id
    try:
        server=start()
        if a.auth_only:
            from support import Harness
            from auth_contract import run_auth_contract
            harness=Harness(dict(locals(),ROOT=ROOT,SDK=SDK))
            run_auth_contract(harness)
            server=harness.server
            print(json.dumps({'passed':len(checks),'auth_only':True,'work_dir':str(work)}))
            return
        check(request('GET','/api/v1/capabilities',auth=None)[2]['registration']=='closed','closed private capabilities')
        check(request('GET','/api/v1/packages',auth=None)[0]==401,'anonymous reads denied')
        check(request('GET','/api/v1/me')[2]['account']['id']==account['account_id'],'bearer identity')
        check(request('POST','/api/v1/publish-sessions',{'unexpected':1})[0]==400,'unknown fields rejected')
        install_options=[dict(name='library-dir', type='path', env='DEMO_LIBRARY_DIR',
                              description='Directory containing the runtime libraries.',
                              default='/opt/demo/lib', probe='demo.so', choices=[],
                              search={'linux':['/usr/local/lib/demo']})]
        payload=fixture(extra={'install_options':install_options})
        key=secrets.token_hex(16)
        first=create(payload,key=key)
        check(first[0]==201,'first reservation')
        id=first[2]['id']
        check(create(payload,key=key)[2]['id']==id,'create idempotency replay')
        check(create(payload,key=key,expected_size=len(payload)+1)[0]==409,'idempotency conflict')
        check(create(payload)[0]==409,'variant reservation conflict')
        check(request('GET','/api/v1/publish-sessions/'+id,auth=other['token'])[0]==404,'session isolation')
        check(upload(id,payload,auth=other['token'])[0]==404,'upload account isolation')
        check(upload(id,payload[:-1]+b'x')[0]==409,'digest mismatch rejected')
        check(upload(id,payload)[0]==200,'upload verified bytes')
        check(upload(id,payload)[0]==200,'identical upload retry')
        commit_key=secrets.token_hex(16)
        check(request('POST','/api/v1/publish-sessions/'+id+'/commit',{},key=commit_key)[0]==202,'asynchronous commit')
        run('worker-once')
        status=request('GET','/api/v1/publish-sessions/'+id)[2]
        check(status['state']=='published','verified publication: '+str(status))
        metadata=request('GET',base+'/versions/1.0.0')[2]
        check(metadata['artifacts'][0]['install_options']==install_options,
              'catalog preserves install parameters from the published manifest')
        check(create(payload)[0]==409,'published variant immutable')
        url=base+'/versions/1.0.0/artifacts/source/download'
        downloaded=request('GET',url,raw=True)
        check(downloaded[0]==200 and downloaded[2]==payload,'exact artifact download')
        check(downloaded[1]['content-length']==str(len(payload)) and 'transfer-encoding' not in downloaded[1],'fixed-length wire framing')
        check(request('HEAD',url)[2]==b'','HEAD has no body')
        check(request('GET',url,headers={'If-None-Match':downloaded[1]['etag']})[0]==304,'conditional download')
        run('worker-once')
        stats=request('GET',base+'/stats/downloads')[2]
        check(sum(r['count'] for r in stats['rows'])==1,'terminal download statistics exclude HEAD/304: '+str(stats))
        source_id=id
        publish(fixture(var='bytecode',mode='bytecode'),var='bytecode')
        publish(fixture(var='mixed',mode='mixed'),var='mixed')
        metadata=request('GET',base+'/versions/1.0.0')[2]
        check(len(metadata['artifacts'])==3,'append variants to common release')
        for ver in ['1.2.0','1.10.0','1.10.0-rc.1']:
            publish(fixture(ver=ver),ver=ver)
        index=request('GET',base+'/index?limit=2')[2]
        check([r['version'] for r in index['versions']]==['1.10.0','1.10.0-rc.1'],'SemVer order')
        from urllib.parse import quote
        page=request('GET',base+'/index?limit=2&cursor='+quote(index['next_cursor'],safe=''))[2]
        check([r['version'] for r in page['versions']]==['1.2.0','1.0.0'],'signed cursor pagination')
        op('quarantine',package_ref='test/demo',version='1.0.0',variant='source',enabled=True)
        check(request('GET',url,headers={'If-None-Match':downloaded[1]['etag']})[0]==423,'quarantine checked before ETag')
        op('quarantine',package_ref='test/demo',version='1.0.0',variant='source',enabled=False)
        bad=b'not a tarball'
        _,_,session=create(bad,ver='9.0.0')
        check(upload(session['id'],bad)[0]==200,'opaque bytes quarantined before inspection')
        request('POST','/api/v1/publish-sessions/'+session['id']+'/commit',{})
        run('worker-once')
        check(request('GET','/api/v1/publish-sessions/'+session['id'])[2]['state']=='rejected','invalid archive rejected by worker')
        check(create(bad,ver='9.0.0')[0]==201,'rejected session releases reservation')
        _,_,cancelled=create(payload,ver='8.0.0')
        cancelpath='/api/v1/publish-sessions/'+cancelled['id']
        check(request('DELETE',cancelpath,{})[2]['state']=='cancelled','cancel session')
        check(upload(cancelled['id'],payload)[0]==409,'cancel fences uploads')
        # Restart with pending validation and verify durable job recovery.
        pending=fixture(ver='2.0.0')
        _,_,pending_session=create(pending,ver='2.0.0')
        upload(pending_session['id'],pending)
        request('POST','/api/v1/publish-sessions/'+pending_session['id']+'/commit',{})
        server.terminate();server.wait(timeout=10);server=start()
        run('worker-once')
        check(request('GET','/api/v1/publish-sessions/'+pending_session['id'])[2]['state']=='published','durable validation after web restart')
        scoped_token=request('POST','/api/v1/tokens',{'label':'read','scopes':['registry:read']})
        check(scoped_token[0]==201,'delegated token')
        read_token=scoped_token[2]['token']
        check(request('POST','/api/v1/publish-sessions',dict(package_ref='test/future',version='1.0.0',variant='source',expected_size=1,expected_sha256='sha256:'+'0'*64),auth=read_token)[0]==403,'read token cannot publish')
        check(request('DELETE','/api/v1/tokens/'+scoped_token[2]['id'],{})[0]==200,'revoke token')
        check(request('GET','/api/v1/me',auth=read_token)[0]==401,'revocation immediately effective')
        credentials=work/'credentials.json'
        credentials.write_text(json.dumps({'origin':f'http://127.0.0.1:{port}','token':token}))
        credentials.chmod(0o600)
        common_cli=['--origin',f'http://127.0.0.1:{port}','--credentials',str(credentials),'--ref','test/demo','--version','1.0.0','--variant','source']
        fetched=work/'fetched.sputnikpkg'
        run('fetch',options=common_cli+['--out',str(fetched)])
        check(fetched.read_bytes()==payload,'Sputnik CLI exact fetch with empty PATH')
        artifact=work/'cli.sputnikpkg'
        cli_payload=fixture(ver='3.0.0')
        artifact.write_bytes(cli_payload)
        worker=None
        if not a.vm:
            worker=subprocess.Popen(command+['--config',str(config),'worker'],cwd=ROOT,env=runtime_env,stdout=log,stderr=log)
            try:
                cli_args=['--origin',f'http://127.0.0.1:{port}','--credentials',str(credentials),'--ref','test/demo','--version','3.0.0','--variant','source','--artifact',str(artifact),'--session',str(work/'cli-session.json')]
                published=run('publish',options=cli_args)
                check(published['state']=='published','Sputnik publisher and worker supervisor with empty PATH')
                check(run('publish',options=cli_args)['id']==published['id'],'Sputnik publisher resumes saved session')
            finally:
                worker.terminate();worker.wait(timeout=10)
            check(worker.returncode==0,'Sputnik supervisor handles termination')
        db=sqlite3.connect(work/'data/metadata.sqlite')
        check(db.execute("SELECT count(*) FROM audit WHERE kind='release_published' AND version='1.0.0'").fetchone()[0]==1,'release counted once for three variants')
        check(db.execute("SELECT count(*) FROM audit WHERE kind='artifact_published' AND version='1.0.0'").fetchone()[0]==3,'artifact publications counted separately')
        check(db.execute('SELECT count(*) FROM tokens WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0]==1,'token digest storage')
        db.close()
        backup=work/'backup'
        run('backup',options=['--out',str(backup)])
        restored_config=work/'restored.json'
        settings=json.loads(config.read_text());settings['data_dir']=str(work/'restored')
        restored_config.write_text(json.dumps(settings))
        run('restore',options=['--backup',str(backup)],selected_config=restored_config)
        restored=sqlite3.connect(work/'restored/metadata.sqlite')
        check(restored.execute('SELECT count(*) FROM artifacts').fetchone()[0]==(7 if a.vm else 8),'Sputnik backup and restore all artifact variants')
        check(restored.execute('SELECT count(*) FROM sessions WHERE reserved=1').fetchone()[0]==0,'restore releases unfinished reservations')
        restored.close()
        server = run_regressions(dict(locals(), ROOT=ROOT, SDK=SDK))
        server = run_conformance(dict(locals(), ROOT=ROOT, SDK=SDK))
        report={'backend':'vm' if a.vm else 'full-native','runtime_sha256':runtime_sha256,'checks':checks,'count':len(checks),'work_dir':str(work)}
        (ROOT/'.build'/('integration-vm.json' if a.vm else 'integration-native.json')).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'passed':len(checks),'work_dir':str(work)}))
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)
        log.close()

if __name__=='__main__':
    main()
