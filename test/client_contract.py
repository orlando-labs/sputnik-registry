"""CLI transport validation against a controllable loopback HTTP origin."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import subprocess
import threading


def run_client_contract(t):
    payload=t.ctx['payload']
    digest='sha256:'+hashlib.sha256(payload).hexdigest()
    path='/api/v1/namespaces/test/packages/demo/versions/1.0.0/artifacts/source'
    state={'mode':'ok','requests':[]}

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def do_GET(self):
            state['requests'].append(self.path)
            mode=state['mode']
            download=self.path.endswith('/download')
            status=200; headers={}; body=payload
            if not download:
                metadata=dict(package_ref='test/demo',version='1.0.0',variant='source',url=origin+path+'/download',size=len(payload),sha256=digest)
                if mode=='identity': metadata['package_ref']='other/demo'
                if mode=='origin': metadata['url']='http://127.0.0.1:1/stolen'
                if mode=='size': metadata['size']=67108865
                if mode=='digest-shape': metadata['sha256']='sha256:bad'
                body=json.dumps(metadata).encode()
                if mode=='metadata-json': body=b'{bad json'
                if mode=='metadata-large': body=b' '*1048577
                if mode=='redirect': status=302; headers['Location']=origin+'/redirected'
            else:
                headers['X-Sputnik-SHA256']=digest
                if mode=='digest': body=b'x'*len(payload)
                if mode=='short': body=payload[:10]
                if mode=='hash-header': headers['X-Sputnik-SHA256']='sha256:'+'0'*64
                if mode=='encoding': headers['Content-Encoding']='gzip'
                if mode=='status': status=206
                if mode=='download-redirect': status=302; headers['Location']=origin+'/redirected'
                if mode=='chunked': headers['Transfer-Encoding']='chunked'
            self.send_response(status)
            for key,value in headers.items(): self.send_header(key,value)
            if mode!='chunked' or not download:
                self.send_header('Content-Length',str(len(payload) if download and mode=='short' else len(body)))
            self.send_header('Connection','close'); self.end_headers()
            try:
                if download and mode=='chunked': self.wfile.write((hex(len(body))[2:]+'\r\n').encode()+body+b'\r\n0\r\n\r\n')
                else: self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError): pass
            self.close_connection=True

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    origin='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    credentials=t.work/'transport-credentials.json'
    credentials.write_text(json.dumps(dict(origin=origin,token='spreg_'+'a'*64))); credentials.chmod(0o600)
    args=['--origin',origin,'--credentials',str(credentials),'--ref','test/demo','--version','1.0.0','--variant','source']
    try:
        for mode in ['ok','identity','origin','size','digest-shape','metadata-json','metadata-large','redirect','download-redirect','digest','short','hash-header','encoding','status','chunked']:
            state['mode']=mode; state['requests']=[]
            output=t.work/('transport-'+mode+'.sputnikpkg')
            result=t.invoke('fetch',args+['--out',str(output)])
            if mode=='ok':
                t.check(result.returncode==0 and output.read_bytes()==payload,'CLI accepts exact verified bytes')
            else:
                t.check(result.returncode!=0 and not output.exists(),'CLI rejects '+mode)
            t.check(not list(t.work.glob(output.name+'.partial-*')),'CLI cleans partial '+mode)
            t.check('/redirected' not in state['requests'],'CLI does not follow redirects '+mode)
        state['mode']='ok'
        output=t.work/'transport-existing'; output.write_bytes(b'keep existing')
        t.check(t.invoke('fetch',args+['--out',str(output)]).returncode!=0 and output.read_bytes()==b'keep existing','CLI never overwrites output')
        t.check(not list(t.work.glob(output.name+'.partial-*')),'existing output failure cleans temporary')
        state['requests']=[]
        credentials.write_text(json.dumps(dict(origin='http://127.0.0.1:1',token='spreg_'+'a'*64)))
        t.check(t.invoke('fetch',args+['--out',str(t.work/'transport-mismatch')]).returncode!=0 and not state['requests'],'credentials reject origin before network')
        credentials.write_text(json.dumps(dict(origin=origin,token='spreg_'+'a'*64))); credentials.chmod(0o644)
        t.check(t.invoke('fetch',args+['--out',str(t.work/'transport-public')]).returncode!=0 and not state['requests'],'credentials require private permissions')
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

    # Interrupt the real publisher after durable commit, then resume the saved session.
    t.reset_rate()
    artifact=t.work/'publisher-resume.sputnikpkg'; artifact.write_bytes(t.artifact('24.0.0'))
    session_file=t.work/'publisher-resume.json'
    args=['--origin','http://127.0.0.1:'+str(t.port),'--credentials',str(t.ctx['credentials']),
          '--ref',t.ref,'--version','24.0.0','--variant','source','--artifact',str(artifact),'--session',str(session_file)]
    process=subprocess.Popen(t.command+['publish']+args,cwd=t.root,env=t.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        def committed():
            rows=t.sql("SELECT id FROM sessions WHERE ref=? AND version='24.0.0' AND state='validating'",(t.ref,))
            return rows[0][0] if rows else None
        sid=t.wait(committed,'publisher commits before interrupt',timeout=120)
        process.kill(); process.communicate(timeout=10)
        t.run('worker-once')
        result=t.invoke('publish',args)
        t.check(result.returncode==0 and json.loads(result.stdout)['id']==sid,'publisher resumes after process kill')
        t.check(t.sql("SELECT count(*) FROM sessions WHERE ref=? AND version='24.0.0'",(t.ref,))[0][0]==1,'resume reuses original session')
    finally:
        if process.poll() is None: process.kill(); process.communicate(timeout=10)
