"""GitHub device protocol fixture and complete SDK login/publish/logout path."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time


def run_auth_contract(t):
    state = dict(mode='ok', subject=81234, login='github-publisher', polls=0, devices=0)
    class GitHub(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get('content-length', 0))))
            if self.path == '/login/device/code':
                assert data == dict(client_id='test-client', scope=''), data
                state['devices']+=1
                doc=dict(device_code=secrets.token_hex(20), user_code='ABCD-EFGH', verification_uri=origin+'/login/device', expires_in=900, interval=1)
            else:
                assert self.path == '/login/oauth/access_token' and data['grant_type']=='urn:ietf:params:oauth:grant-type:device_code'
                state['polls']+=1
                if state['mode']=='slow': doc=dict(error='slow_down', interval=6)
                elif state['mode']=='pending': doc=dict(error='authorization_pending')
                elif state['mode']=='denied': doc=dict(error='access_denied')
                elif state['mode']=='expired': doc=dict(error='expired_token')
                else:
                    time.sleep(.1)
                    doc=dict(access_token='fixture-github-access-token',token_type='bearer')
            self.send(doc)
        def do_GET(self):
            assert self.path == '/user' and self.headers.get('authorization')=='Bearer fixture-github-access-token'
            self.send(dict(id=state['subject'],login=state['login'],type='User'))
        def send(self, doc):
            body=json.dumps(doc).encode(); self.send_response(200)
            self.send_header('content-length',str(len(body))); self.end_headers(); self.wfile.write(body)
    provider=ThreadingHTTPServer(('127.0.0.1',0),GitHub)
    origin=f'http://127.0.0.1:{provider.server_port}'
    thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
    original=json.loads(t.config.read_text())
    t.env['SPUTNIK_REGISTRY_TEST_GITHUB_ORIGIN']=origin
    def start(**fields):
        data=dict(provider='github');data.update(fields)
        result=t.request('POST','/api/v1/auth/device',data,auth=None)
        t.check(result[0]==201,'device login start (HTTP '+str(result[0])+')')
        return result[2]
    def request(attempt, action, data=None, **kw):
        return t.request('DELETE' if action=='' else 'POST', '/api/v1/auth/device/'+attempt['attempt_id']+('/'+action if action else ''),
            data if data is not None else {}, auth=attempt['login_secret'], **kw)
    def poll(attempt):
        t.sql('UPDATE device_logins SET next_poll=0 WHERE id=?',(attempt['attempt_id'],))
        return request(attempt,'poll')
    try:
        t.error(t.request('POST','/api/v1/auth/device',dict(provider='github'),auth=None),503,'LOGIN_DISABLED','unconfigured GitHub fails closed')
        t.restart(github_client_id='test-client',registration='open')
        caps=t.request('GET','/api/v1/capabilities',auth=None)[2]
        t.check(caps['registration']=='open' and caps['login_providers']==['github'] and 'device-login' in caps['features'],'configured device login capabilities')
        attempt=start(); aid=attempt['attempt_id']
        t.check(t.sql('SELECT secret_digest FROM device_logins WHERE id=?',(aid,))[0][0]==hashlib.sha256(attempt['login_secret'].encode()).hexdigest(),'login secret stored as hash')
        t.error(t.request('POST','/api/v1/auth/device/'+aid+'/poll',{},auth='ABCD-EFGH'),401,'UNAUTHORIZED','user code is not a credential')
        t.error(t.request('GET','/api/v1/me',auth=attempt['login_secret']),401,'UNAUTHORIZED','login secret cannot authorize package API')
        t.error(request(attempt,'complete',dict(namespace='github_pub')),409,'LOGIN_NOT_VERIFIED','unverified login cannot issue API token')
        before=state['polls']; result=request(attempt,'poll')
        t.check(result[0]==200 and result[2]['state']=='pending' and state['polls']==before,'poll obeys initial GitHub interval')
        state['mode']='slow'; result=poll(attempt)
        t.check(result[0]==200 and result[2]['retry_after']==6,'GitHub slow_down increases interval')
        state['mode']='ok';t.sql('UPDATE device_logins SET next_poll=0 WHERE id=?',(aid,));before=state['polls']
        with ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(lambda _:request(attempt,'poll'),range(3)))
        t.check(all(x[0]==200 for x in results) and state['polls']==before+1,'concurrent polls fence one GitHub request')
        t.check(request(attempt,'poll')[2]['state']=='verified','GitHub identity verified')
        count=t.sql('SELECT count(*) FROM accounts')[0][0]
        for namespace in ['admin','test']:
            t.error(request(attempt,'complete',dict(namespace=namespace)),409,'NAMESPACE_UNAVAILABLE','reserved/occupied namespace rejected '+namespace)
        t.error(request(attempt,'complete',dict(namespace='github_pub',token=dict(scopes=['namespace:publish:test']))),403,'SCOPE_ESCALATION','login scopes cannot escape owned namespace')
        t.check(t.sql('SELECT count(*) FROM accounts')[0][0]==count,'failed completion rolls back account creation')
        completed=request(attempt,'complete',dict(namespace='github_pub'))
        t.check(completed[0]==201,'GitHub account and token created atomically: '+str(completed[2].keys()))
        account=completed[2];token=account['token']
        t.check(t.request('GET','/api/v1/me',auth=token)[2]['account']['id']==account['account_id'],'GitHub token authenticates')
        t.check(not t.sql('SELECT * FROM grants WHERE account_id=?',(account['account_id'],)),'GitHub login grants no native or root privileges')
        t.error(request(attempt,'complete',dict(namespace='github_pub')),409,'TOKEN_SECRET_ALREADY_ISSUED','completion never replays token secret')
        state['login']='renamed-github-user';again=start();verified=poll(again)[2]
        t.check(verified['account_id']==account['account_id'] and verified['namespace']=='github_pub','GitHub rename keeps numeric subject and namespace')
        request(again,'')
        mismatch=start(expected_account_id=t.ctx['account']['account_id'])
        t.check(poll(mismatch)[2]['error']['code']=='ACCOUNT_MISMATCH','expected account prevents identity switch')
        request(mismatch,'')
        request(attempt,'');request(attempt,'')
        t.check(t.request('GET','/api/v1/me',auth=token)[0]==401,'cancel revokes only token issued by attempt')
        for mode,terminal in [('denied','denied'),('expired','expired')]:
            state['mode']=mode;item=start();t.check(poll(item)[2]['state']==terminal,'GitHub '+mode+' terminates login')
        state['mode']='ok';item=start()
        t.sql('UPDATE device_logins SET expires_at=0 WHERE id=?',(item['attempt_id'],))
        t.error(request(item,'poll'),410,'LOGIN_EXPIRED','expired attempt rejected')
        item=start();t.restart(github_client_id='test-client',registration='open')
        t.error(poll(item),409,'LOGIN_RESTARTED','restart discards pending GitHub device code')
        t.restart(github_client_id='test-client',registration='closed');state['subject']=998877
        closed=start();t.check(poll(closed)[2]['error']['code']=='REGISTRATION_CLOSED','closed registration rejects unknown GitHub identity')
        t.op('github-link',account_id=t.ctx['account']['account_id'],subject='998877')
        linked=start();t.check(poll(linked)[2]['account_id']==t.ctx['account']['account_id'],'operator links manual account for closed GitHub login')
        linked_token=request(linked,'complete',{})[2]['token']
        t.check(t.request('GET','/api/v1/me',auth=linked_token)[0]==200,'console-provisioned account can log in through GitHub')
        request(linked,'')
        state['subject']=81234;t.restart(github_client_id='test-client',registration='open')
        t.reset_rate()
        env=dict(os.environ,HOME=str(t.work/'github-cli-home'),SPUTNIK_PKG_HOME=str(t.work/'github-cli-packages'),SPUTNIK_PKG_REGISTRY=f'http://127.0.0.1:{t.port}')
        env.pop('SPUTNIK_PKG_CREDENTIALS',None)
        sdk=str(t.ctx['SDK'])
        def cli(*args, ok=True):
            result=subprocess.run([sdk,'pkg',*args],env=env,capture_output=True,text=True,timeout=180)
            t.check((result.returncode==0)==ok,'SDK '+args[0]+': '+result.stderr[-900:])
            return result
        cli('login','--namespace','github_pub')
        files=list((t.work/'github-cli-home/.sputnik/credentials').glob('*.json'))
        t.check(len(files)==1 and files[0].stat().st_mode&0o077==0,'SDK login saves private per-origin credentials')
        saved=json.loads(files[0].read_text())
        t.check('token' in saved and saved['namespace']=='github_pub','saved credential is registry token')
        cli('whoami')
        t.sql('UPDATE tokens SET expires_at=0 WHERE id=?',(saved['token_id'],))
        cli('login','--namespace','github_pub')
        payload=t.artifact('1.0.0',ref='github_pub/sdk_publish')
        artifact=t.work/'github-publish.sputnikpkg';artifact.write_bytes(payload)
        publish=subprocess.Popen([sdk,'pkg','publish',str(artifact)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            t.wait(lambda:t.sql("SELECT 1 FROM sessions WHERE ref='github_pub/sdk_publish' AND state='validating'"),'SDK publish reaches worker',timeout=120)
            t.run('worker-once')
            stdout,stderr=publish.communicate(timeout=120)
            t.check(publish.returncode==0 and 'Published github_pub/sdk_publish@1.0.0' in stdout,'SDK GitHub login to publication: '+stderr[-1000:])
        finally:
            if publish.poll() is None:publish.terminate();publish.wait(timeout=10)
        cli('publish',str(artifact))
        cli('install','github_pub/sdk_publish','--version','1.0.0')
        active=json.loads(files[0].read_text())['token']
        cli('logout')
        t.check(not files[0].exists() and t.request('GET','/api/v1/me',auth=active)[0]==401,'SDK logout revokes token and removes credentials')
        manual=t.op('token-issue',account_id=t.ctx['account']['account_id'],label='console-after-github',scopes=['registry:read'])
        t.check(t.request('GET','/api/v1/me',auth=manual['token'])[0]==200,'console token issuance remains functional')
        t.check(not t.sql("SELECT sql FROM sqlite_master WHERE sql LIKE '%device_code%' OR sql LIKE '%access_token%'"),'GitHub secrets have no persistent DB column')
    finally:
        t.env.pop('SPUTNIK_REGISTRY_TEST_GITHUB_ORIGIN',None)
        t.server.terminate();t.server.wait(timeout=10)
        t.config.write_text(json.dumps(original));t.server=t.ctx['start']()
        provider.shutdown();provider.server_close();thread.join()
