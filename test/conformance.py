"""HTTP schemas, admission, quotas, pagination, cancellation and lifecycle gates."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import secrets
import socket
import subprocess
import tarfile
import time
from urllib.parse import quote
from support import Harness


def inputs(t):
    base = t.ctx['base']
    for field in ('from','to'):
        for value in ['garbage','2026-02-30','2026-13-01','1900-02-29','0000-01-01','2026-1-01','2026-10-21T00:00:00Z','2026-01-00']:
            t.error(t.request('GET',base+'/stats/downloads?'+field+'='+value),400,'INVALID_DATE','reject date '+field+'='+value)
    for start,end,status in [('2000-02-29','2000-03-01',200),('2024-01-01','2025-01-01',200),('2024-01-01','2025-01-02',400),('2026-01-01','2026-01-01',400)]:
        t.check(t.request('GET',base+'/stats/downloads?from='+start+'&to='+end)[0]==status,'calendar interval '+start+'/'+end)
    for endpoint in [base+'/index','/api/v1/packages']:
        for limit in ['0','101','9'*80,'-1','1.5','abc']:
            t.error(t.request('GET',endpoint+'?limit='+limit),400,'INVALID_PAGE','bounded page limit '+limit[:12])
        for limit in ['1','100','0'*80+'1']:
            t.check(t.request('GET',endpoint+'?limit='+limit)[0]==200,'valid page limit '+limit[:12])
    manager=t.op('token-issue',account_id=t.ctx['account']['account_id'],label='owners-contract',scopes=['registry:read','package:owners:test/demo'])['token']
    for target in [None,42,[],{},'', 'x'*256, 'G'*32]:
        t.error(t.request('POST',base+'/owners',dict(account_id=target,role='publisher'),auth=manager),400,'INVALID_ACCOUNT','owner identifier schema '+repr(target)[:20])
    t.error(t.request('POST',base+'/owners',{'role':'owner'},auth=manager),400,'INVALID_REQUEST','missing owner identifier')
    t.error(t.request('POST',base+'/owners',dict(account_id=t.ctx['account']['account_id'],role='admin'),auth=manager),400,'INVALID_ROLE','reject unknown ACL role')
    t.check(t.request('POST',base+'/owners',dict(account_id='0'*32,role='publisher'),auth=manager)[0] in (401,404),'unknown account cannot join owners')


def cancelled_upload(t):
    payload=t.ctx['payload']
    sid=t.create(payload,'21.0.0')[2]['id']; path='/api/v1/publish-sessions/'+sid
    sock=socket.create_connection(('127.0.0.1',t.port),timeout=15)
    try:
        sock.sendall(('PUT '+path+'/artifact HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer '+t.token+'\r\nContent-Type: application/vnd.sputnik.package.v1\r\nContent-Length: '+str(len(payload))+'\r\n\r\n').encode()+payload[:1])
        t.wait(lambda:t.sql('SELECT state FROM sessions WHERE id=?',(sid,))[0][0]=='receiving','upload receives')
        key=secrets.token_hex(16)
        for cancel_key in [key,key,secrets.token_hex(16)]:
            t.check(t.cancel(sid,cancel_key)[0]==200,'cancel/replay accepted')
            row=t.sql('SELECT reserved,receiving_until FROM sessions WHERE id=?',(sid,))[0]
            t.check(row[0]==1 and row[1]>time.time(),'cancel retains live writer credit')
        t.error(t.create(payload,'21.0.0'),409,'ARTIFACT_RESERVED','cancelled live upload retains variant')
        sock.sendall(payload[1:]); r=http.client.HTTPResponse(sock); r.begin(); r.read()
        t.check(r.status==409,'cancel fences upload finalize'); r.close()
        t.wait(lambda:t.sql('SELECT reserved,receiving_until FROM sessions WHERE id=?',(sid,))[0]==(0,0),'credit refunded after cleanup')
        t.check(not list((t.data/'staging').glob(t.sql('SELECT worker_fence FROM sessions WHERE id=?',(sid,))[0][0] or 'none')),'cancelled staging cleaned')
        replacement=t.create(payload,'21.0.0'); t.check(replacement[0]==201,'variant reusable after writer stops'); t.cancel(replacement[2]['id'])
    finally: sock.close()


def admission(t):
    t.reset_rate()
    name, library, target, provider=t.library()
    files={'main.s':'package test.contract\nexport answer\ndef answer(): 42\n',name:library}
    for bundle in ['none','runtime']:
        ref='test/provider_'+bundle
        payload=t.artifact('1.0.0',ref=ref,fields={'bundle': bundle},extra={**target, **provider},files=files)
        state=t.publish(payload,'1.0.0',ref=ref)
        t.check(state.get('reason_code')=='NATIVE_GRANT_REQUIRED','library provider requires native grant '+bundle)
        t.op('grant',account_id=t.ctx['account']['account_id'],kind='native',target=ref)
        state=t.publish(payload,'1.0.0',ref=ref)
        t.check(state['state']=='published','granted library provider publishes '+bundle)
        base='/api/v1/namespaces/test/packages/provider_'+bundle
        metadata=t.request('GET',base+'/versions/1.0.0/artifacts/source')[2]
        compact=t.request('GET',base+'/index')[2]['versions'][0]['artifacts'][0]
        t.check(metadata['native_payload'] and metadata['native_mode']=='prebuilt' and compact['native_payload'],'provider facts survive exact and compact metadata')
    # A manifest mismatch must win over a hostile later member: proves early admission.
    payload=t.artifact('21.1.0',fields={'limit_profile': 'native-large'})
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        manifest=archive.extractfile('sputnik.build.json').read()
    malformed=io.BytesIO()
    with tarfile.open(fileobj=malformed,mode='w:gz') as archive:
        item=tarfile.TarInfo('sputnik.build.json'); item.size=len(manifest); archive.addfile(item,io.BytesIO(manifest))
        item=tarfile.TarInfo('../hostile'); item.size=1; archive.addfile(item,io.BytesIO(b'x'))
    state=t.publish(malformed.getvalue(),'21.1.0')
    t.check(state.get('reason_code')=='INVALID_ARTIFACT','standard session rejects a larger inspector profile')
    # SDK test asserts the precise early profile error before the hostile member.
    for section in ['dependencies','artifact.dependencies','artifact.build-dependencies']:
        for ref,version,code in [('test/sdk','not-semver','INVALID_VERSION'),('UPPER/sdk','1.0.0','INVALID_REFERENCE')]:
            ver='21.2.'+str(admission.counter); admission.counter+=1
            payload=t.artifact(ver,extra={section: {ref: version}})
            t.check(t.publish(payload,ver).get('reason_code')==code,'validate '+section+' '+code)
    payload=t.artifact('21.2.10',extra={'dependencies': {'test/sdk': '1.0.0'}, 'artifact': {'build-dependencies': {'test/sdk': '2.0.0'}}})
    t.check(t.publish(payload,'21.2.10').get('reason_code')=='DEPENDENCY_CONFLICT','combined exact dependency conflict')
    for count in [256,257]:
        ver='21.2.'+str(count)
        extra={'artifact': {'build_dependencies': {'test/d'+str(i): '1.0.0' for i in range(count)}}}
        result=t.publish(t.artifact(ver,extra=extra),ver)
        t.check(result['state']=='published' if count==256 else result.get('reason_code')=='METADATA_LIMIT','combined dependency budget '+str(count))
admission.counter=0


def quota_owner(t):
    t.reset_rate(); ref='contract_quota'; actors=[]
    for account in [t.ctx['account'],t.ctx['other']]:
        t.op('grant',account_id=account['account_id'],kind='unscoped',target=ref)
        actors.append(t.op('token-issue',account_id=account['account_id'],label='quota',scopes=['registry:read','package:publish:'+ref,'package:owners:'+ref])['token'])
    payload=t.artifact('1.0.0',ref=ref)
    initial=t.create(payload,'1.0.0',ref=ref,auth=actors[0]); sid=initial[2]['id']
    t.check(t.create(payload,'1.0.1',ref=ref,auth=actors[1])[0]==403,'first unscoped reservations retain one owner')
    t.upload(sid,payload,actors[0]); t.request('POST','/api/v1/publish-sessions/'+sid+'/commit',{},auth=actors[0]); t.run('worker-once')
    t.check(t.session(sid)['state']=='published','initial quota package publishes')
    t.request('POST','/api/v1/packages/'+ref+'/owners',dict(account_id=t.ctx['other']['account_id'],role='owner'),auth=actors[0])
    original=t.sql('SELECT size FROM artifacts WHERE ref=?',(ref,))[0][0]
    t.sql('UPDATE artifacts SET size=? WHERE ref=?',(10737418240,ref))
    try:
        for auth in actors:
            t.error(t.create(payload,'1.0.1',ref=ref,auth=auth),429,'STORAGE_QUOTA','all owners share stored quota')
    finally: t.sql('UPDATE artifacts SET size=? WHERE ref=?',(original,ref))
    state=t.publish(t.artifact('1.0.1',ref=ref),'1.0.1',ref=ref,auth=actors[1])
    t.check(state['state']=='published','co-owner can publish below quota')
    quotas=t.sql('SELECT DISTINCT quota_owner FROM sessions WHERE ref=?',(ref,))
    t.check(quotas==[('account:'+t.ctx['account']['account_id'],)],'co-owner session uses persisted quota owner')


def backup_lifecycle(t):
    for stage in ['migrate','account-create']:
        settings=json.loads(t.config.read_text()); settings['data_dir']=str(t.work/('fresh-'+stage))
        config=t.work/('fresh-'+stage+'.json'); config.write_text(json.dumps(settings))
        options=['--label','Fresh','--namespace','fresh'] if stage=='account-create' else []
        t.run(stage,options=options,selected_config=config)
        backup=t.work/('fresh-backup-'+stage)
        result=t.invoke('backup',['--out',str(backup)],config=config)
        t.check(result.returncode==0,'backup before usage initialization '+stage+': '+result.stderr[-200:])
        settings['data_dir']=str(t.work/('fresh-restored-'+stage)); config.write_text(json.dumps(settings))
        result=t.invoke('restore',['--backup',str(backup)],config=config)
        t.check(result.returncode==0,'restore fresh store '+stage)
        t.check(t.invoke('worker-once',config=config).returncode==0,'worker initializes restored projection '+stage)
        t.check(t.invoke('backup',['--out',str(backup)],config=config).returncode!=0,'backup cannot overwrite existing directory')


def pagination(t):
    t.reset_rate()
    versions=['22.0.0-alpha','22.0.0-alpha.1','22.0.0-alpha.beta','22.0.0-beta','22.0.0-beta.2','22.0.0-beta.11','22.0.0-rc.1','22.0.0']
    for ver in versions:
        t.check(t.publish(t.artifact(ver),ver)['state']=='published','publish semver '+ver)
    first=t.request('GET',t.base+'/index?limit=3')[2]
    t.check([r['version'] for r in first['versions']]==list(reversed(versions))[:3],'SemVer numeric/alphanumeric ordering')
    cursor=first['next_cursor']
    for value,endpoint in [(cursor[:-1]+('0' if cursor[-1]!='0' else '1'),t.base+'/index'),(cursor,'/api/v1/packages')]:
        t.error(t.request('GET',endpoint+'?cursor='+quote(value,safe='')),400,'INVALID_CURSOR','cursor tamper and target binding')
    encoded,signature=cursor.split('.')
    data=json.loads(base64.b64decode(encoded)); data['expires_at']=0
    text=json.dumps(data,separators=(',',':')).encode()
    secret=json.loads(t.config.read_text())['cursor_secret'].encode()
    expired=base64.b64encode(text).decode()+'.'+hmac.new(secret,text,hashlib.sha256).hexdigest()
    t.error(t.request('GET',t.base+'/index?cursor='+quote(expired,safe='')),400,'INVALID_CURSOR','cursor expiry')
    t.op('quarantine',package_ref=t.ref,version=versions[0],enabled=True)
    t.error(t.request('GET',t.base+'/index?cursor='+quote(cursor,safe='')),409,'INDEX_CHANGED','cursor rejects changed revision')
    t.op('quarantine',package_ref=t.ref,version=versions[0],enabled=False)
    search=t.request('GET','/api/v1/packages?query=contract&limit=1')[2]
    t.check(len(search['packages'])==1 and search['next_cursor'],'search paginates matching references')
    next_page=t.request('GET','/api/v1/packages?query=contract&limit=1&cursor='+quote(search['next_cursor'],safe=''))[2]
    t.check(next_page['packages'][0]['ref']>search['packages'][0]['ref'],'search cursor advances')


def public_catalog(t):
    ref = 'test/public_install'
    payload = t.artifact('1.0.0', ref=ref)
    t.check(t.publish(payload, '1.0.0', ref=ref)['state'] == 'published', 'public install fixture publishes')
    t.check(t.invoke('promote', ['--ref', ref]).returncode == 0, 'public install fixture has root alias')
    account = t.ctx['account']['account_id']
    expired = t.op('token-issue', account_id=account, label='expired-reader', scopes=['registry:read'])
    t.sql('UPDATE tokens SET expires_at=0 WHERE id=?', (expired['id'],))
    no_read = t.op('token-issue', account_id=account, label='write-only', scopes=['token:manage'])
    root = '/api/v1/packages/public_install'
    scoped = '/api/v1/namespaces/test/packages/public_install'
    artifact = scoped + '/versions/1.0.0/artifacts/source'
    search = '/api/v1/packages?query=public_install'
    t.error(t.request('GET', root+'/index', auth=expired['token']), 401, 'UNAUTHORIZED', 'private catalog rejects expired credential')
    t.error(t.request('GET', root+'/index', auth=no_read['token']), 403, 'FORBIDDEN', 'private catalog requires read scope')

    t.restart(read_access='public')
    try:
        t.reset_rate()
        for label, auth in [('anonymous', None), ('expired', expired['token']), ('invalid', 'invalid'),
                            ('write-only', no_read['token']), ('authenticated', t.token)]:
            for path in (search, root, root+'/index', scoped+'/index', scoped+'/versions/1.0.0', artifact):
                t.check(t.request('GET', path, auth=auth)[0] == 200, label+' can read '+path)
            download = artifact+'/download'
            t.check(t.request('HEAD', download, auth=auth)[0] == 200, label+' can inspect download')
            t.check(t.request('GET', download, auth=auth, raw=True)[2] == payload, label+' can download package')
        t.check(t.sql("SELECT count(*) FROM rate_limits WHERE bucket LIKE 'read:%'")[0][0] == 0,
                'public catalog uses anonymous limits even with a valid bearer')

        for path in ('/api/v1/me', '/api/v1/me/usage', '/api/v1/tokens', scoped+'/stats/publications'):
            for auth in (None, expired['token']):
                t.error(t.request('GET', path, auth=auth), 401, 'UNAUTHORIZED', 'public mode protects '+path)
        t.error(t.request('POST', '/api/v1/publish-sessions', {}, auth=expired['token']),
                401, 'UNAUTHORIZED', 'public mode denies publishing with expired credentials')

        origin = f'http://127.0.0.1:{t.port}'
        home = t.work/'public-catalog-home'
        credentials = home/'.sputnik'/'credentials'/(hashlib.sha256(origin.encode()).hexdigest()+'.json')
        credentials.parent.mkdir(parents=True)
        credentials.write_text(json.dumps(dict(origin=origin, token=expired['token'])))
        credentials.chmod(0o600)
        package_home = t.work/'public-catalog-packages'
        env = dict(t.env, HOME=str(home), SPUTNIK_PKG_HOME=str(package_home), SPUTNIK_PKG_CREDENTIALS='')
        args = [str(t.ctx['SDK']), 'pkg', 'install', 'public_install', '--registry', origin]
        installed = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
        t.check(installed.returncode == 0, 'SDK installs with expired saved credentials: '+installed.stderr[-600:])
        t.check((package_home/ref/'1.0.0'/'source'/'test-public_install-1.0.0.sputnikpkg').read_bytes() == payload,
                'SDK stores the published archive bytes')
    finally:
        t.restart(read_access='authenticated')


def access_and_budgets(t):
    t.reset_rate()
    for auth in ['invalid','spreg_'+'0'*64]:
        t.error(t.request('GET','/api/v1/me',auth=auth),401,'UNAUTHORIZED','invalid bearer rejected')
    t.check(t.request('GET',t.base,auth=None,headers={'X-Forwarded-User':t.ctx['account']['account_id'],'X-Forwarded-For':'127.0.0.1'})[0]==401,'forwarded headers do not authenticate')
    t.restart(read_access='public')
    try:
        t.check(t.request('GET',t.base,auth=None)[0]==200,'explicit public mode permits anonymous metadata')
        for path in ['/api/v1/me','/api/v1/me/usage','/api/v1/tokens']:
            t.check(t.request('GET',path,auth=None)[0]==401,'public mode protects '+path)
        t.check(t.request('POST','/api/v1/publish-sessions',{},auth=None)[0]==401,'public mode protects writes')
    finally: t.restart(read_access='authenticated')
    payload=t.artifact('23.0.0')
    t.sql("UPDATE namespaces SET active=0 WHERE slug='test'")
    try: t.check(t.create(payload,'23.0.0')[0]==403,'suspended namespace blocks publication')
    finally: t.sql("UPDATE namespaces SET active=1 WHERE slug='test'")
    account=t.ctx['account']['account_id']
    t.sql('UPDATE accounts SET active=0 WHERE id=?',(account,))
    try: t.check(t.request('GET',t.base)[0]==401,'suspended account cannot read')
    finally: t.sql('UPDATE accounts SET active=1 WHERE id=?',(account,))
    t.sql('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)',('read:'+account,int(time.time())//60,1000))
    t.error(t.request('GET',t.base),429,'RATE_LIMITED','read rate limit')
    t.sql('UPDATE rate_limits SET window=window-1')
    t.check(t.request('GET',t.base)[0]==200,'read rate resets with window')
    t.reset_rate()
    t.error(t.create(payload,'23.0.0',limit_profile='native-large'),403,'FORBIDDEN','disabled native-large profile')
    t.restart(native_large=True)
    try:
        t.error(t.create(payload,'23.0.0',limit_profile='native-large'),403,'FORBIDDEN','enabled native-large still requires grant')
        exact='test/native_large_exact'
        t.op('grant',account_id=account,kind='native-large',target=exact)
        large_payload=t.artifact('1.0.0',ref=exact,fields={'limit_profile': 'native-large'})
        published=t.publish(large_payload,'1.0.0',ref=exact,limit_profile='native-large')
        t.check(published['state']=='published','exact native-large grant covers upload, commit and promotion')
        t.error(t.create(payload,'23.0.0',limit_profile='native-large'),403,'FORBIDDEN','exact native-large grant cannot authorize another package')
        pending=t.create(large_payload,'1.0.1',ref=exact,limit_profile='native-large')
        t.check(pending[0]==201,'exact native-large grant reserves only its package')
        t.sql('DELETE FROM grants WHERE account_id=? AND kind=? AND target=?',(account,'native-large',exact))
        t.error(t.upload(pending[2]['id'],large_payload),403,'FORBIDDEN','revoked exact native-large grant fences an existing upload')
        t.cancel(pending[2]['id'])
        t.op('grant',account_id=account,kind='native-large',target='*')
        result=t.create(payload,'23.0.0',limit_profile='native-large')
        t.check(result[0]==201,'granted native-large profile admits'); t.cancel(result[2]['id'])
    finally: t.restart(native_large=False)
    # Persisted quota fixtures avoid allocating large files or waiting for TTLs.
    seed=t.create(payload,'23.0.1')[2]['id']
    try:
        for size,code in [(201326592,'PENDING_QUOTA'),(536870912,'PENDING_QUOTA')]:
            t.sql('UPDATE sessions SET size=? WHERE id=?',(size,seed))
            t.error(t.create(payload,'23.0.2'),429,code,'pending bytes budget '+str(size))
        t.sql('UPDATE sessions SET size=? WHERE id=?',(len(payload),seed))
        t.sql('UPDATE sessions SET receiving_until=? WHERE id=?',(int(time.time())+120,seed))
        other=t.create(payload,'23.0.2')[2]['id']
        t.error(t.upload(other,payload),503,'UPLOAD_CAPACITY','account concurrent upload cap'); t.cancel(other)
    finally:
        t.sql('UPDATE sessions SET receiving_until=0 WHERE id=?',(seed,)); t.cancel(seed)
    t.reset_rate()


def run_conformance(ctx):
    t=Harness(ctx)
    try:
        inputs(t)
        cancelled_upload(t)
        admission(t)
        quota_owner(t)
        backup_lifecycle(t)
        pagination(t)
        public_catalog(t)
        access_and_budgets(t)
        from root_alias_contract import run_root_alias_contract
        run_root_alias_contract(t)
        from client_contract import run_client_contract
        run_client_contract(t)
        from fault_contract import run_fault_contract
        run_fault_contract(t)
        from auth_contract import run_auth_contract
        run_auth_contract(t)
        return t.server
    except BaseException:
        if t.server.poll() is None:
            t.server.terminate(); t.server.wait(timeout=10)
        raise
