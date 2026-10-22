"""Shared real-process fixtures for registry contract and failure tests."""
import hashlib
import json
import platform
import secrets
import sqlite3
import subprocess
import time


class Harness:
    def __init__(self, ctx):
        self.ctx = ctx
        for name in ('work', 'config', 'command', 'token', 'server', 'port'):
            setattr(self, name, ctx[name])
        self.root = ctx['ROOT']
        self.env = ctx['runtime_env']
        self.check = ctx['check']
        self.request = ctx['request']
        self.op = ctx['op']
        self.run = ctx['run']
        self.data = self.work/'data'
        self.ref = 'test/contract'
        self.base = '/api/v1/namespaces/test/packages/contract'

    def sql(self, statement, params=(), usage=False):
        with sqlite3.connect(self.data/('usage.sqlite' if usage else 'metadata.sqlite'), timeout=10) as db:
            return db.execute(statement, params).fetchall()

    def error(self, result, status, code, label):
        actual, headers, body = result
        valid = (actual == status and body.get('error', {}).get('code') == code
                   and headers.get('x-request-id') == body['error'].get('request_id')
                   and headers.get('cache-control') == 'no-store')
        self.check(valid, label if valid else label+': '+str(result)[:400])

    def invoke(self, mode, options=(), config=None, env=None):
        return subprocess.run(self.command+['--config', str(config or self.config), mode]+list(options),
            cwd=self.root, env=env or self.env, capture_output=True, text=True, timeout=240)

    def reset_rate(self):
        self.sql('DELETE FROM rate_limits')

    def wait(self, predicate, label, timeout=30):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(.025)
        raise AssertionError(label)

    def artifact(self, version, variant='source', ref=None, extra='', fields='', files=None, native=False):
        ref = ref or self.ref
        directory = self.work/('fixture-'+secrets.token_hex(6)); directory.mkdir()
        module = (ref.split('/')[0]+'.' if '/' in ref else '')+'contract'
        files = files or {'main.s': 'package '+module+'\nexport answer\ndef answer(): 42\n'}
        for name, value in files.items():
            path = directory/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value if isinstance(value, bytes) else value.encode())
        text = 'schema="sputnik.package-manifest.v1"\n[package]\nname='+json.dumps(ref)+'\nversion='+json.dumps(version)+'\n'
        text += 'kind="native"\n' if native else 'root='+json.dumps(module)+'\n'
        text += '[artifact]\nvariant='+json.dumps(variant)+'\n'+fields
        if not native:
            text += '[[modules]]\nname='+json.dumps(module)+'\nsource="main.s"\n'
        (directory/'sputnik.toml').write_text(text+extra)
        output = directory/'package.sputnikpkg'
        result = subprocess.run([str(self.ctx['SDK']), 'pkg', 'build', str(directory), '--out', str(output), '--variant', variant], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError('fixture build: '+result.stderr+result.stdout)
        return output.read_bytes()

    def create(self, payload, version, variant='source', ref=None, auth=None, **fields):
        data = dict(package_ref=ref or self.ref, version=version, variant=variant,
                    expected_size=len(payload), expected_sha256='sha256:'+hashlib.sha256(payload).hexdigest())
        data.update(fields)
        return self.request('POST', '/api/v1/publish-sessions', data, auth=auth or self.token)

    def upload(self, sid, payload, auth=None):
        return self.ctx['upload'](sid, payload, auth=auth or self.token)

    def session(self, sid, auth=None):
        return self.request('GET','/api/v1/publish-sessions/'+sid, auth=auth or self.token)[2]

    def cancel(self, sid, key=None):
        return self.request('DELETE','/api/v1/publish-sessions/'+sid, {}, key=key)

    def queue(self, payload, version, **kw):
        auth = kw.get('auth')
        result = self.create(payload, version, **kw)
        self.check(result[0] == 201, 'create '+version+': '+str(result[2]))
        sid = result[2]['id']
        self.check(self.upload(sid, payload, auth)[0] == 200, 'upload '+version)
        self.check(self.request('POST','/api/v1/publish-sessions/'+sid+'/commit', {}, auth=auth or self.token)[0] == 202, 'commit '+version)
        return sid

    def publish(self, payload, version, **kw):
        sid = self.queue(payload, version, **kw)
        self.run('worker-once')
        return self.session(sid, kw.get('auth'))

    def restart(self, **settings):
        self.server.terminate(); self.server.wait(timeout=10)
        config = json.loads(self.config.read_text()); config.update(settings)
        self.config.write_text(json.dumps(config))
        self.server = self.ctx['start']()

    def library(self):
        directory = self.work/'provider-library'; directory.mkdir(exist_ok=True)
        (directory/'lib.c').write_text('int contract_answer(void) { return 42; }\n')
        mac = platform.system() == 'Darwin'
        name = 'libcontract.dylib' if mac else 'libcontract.so'
        flags = ['-dynamiclib', '-mmacosx-version-min=13.0', '-Wl,-install_name,@rpath/'+name] if mac else ['-shared', '-fPIC']
        result = subprocess.run(['clang']+flags+[str(directory/'lib.c'), '-o', str(directory/name)], capture_output=True, text=True)
        self.check(result.returncode == 0, 'build provider fixture library')
        arch = 'aarch64' if platform.machine() in ('arm64','aarch64') else 'x86_64'
        target = '[target]\nos="'+('macos' if mac else 'linux')+'"\narch="'+arch+'"\n'
        if mac: target += 'min_os="13.0"\n'
        else: target += 'libc='+json.dumps('musl' if platform.libc_ver()[0] == 'musl' else 'gnu')+'\n'
        provider = '[[native_providers]]\nid="contract-runtime"\nkind="runtime"\nversion="1.0.0"\nabi="contract-1"\nbuild_id="sha256:'+'a'*64+'"\nfiles=["'+name+'"]\n[[native_providers.libraries]]\npath="'+name+'"\nrole="runtime"\nabi="contract-1"\n'
        return name, (directory/name).read_bytes(), target, provider
