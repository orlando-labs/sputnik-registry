"""Publication, authorization, storage and recovery regression scenarios.

Run through integration.py, against the same real native/VM web and worker.
SQL is used only for deterministic clock/budget/crash-window setup; requests,
artifact validation, process termination, publication and restore use public APIs.
"""
from manifest import write_source_manifest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import secrets
import os
import socket
import sqlite3
import subprocess
import platform
import tarfile
import time


def run_regressions(ctx):
    check, request, run, op = (ctx[k] for k in ('check', 'request', 'run', 'op'))
    create, upload, fixture, publish = (ctx[k] for k in ('create', 'upload', 'fixture', 'publish'))
    work, root, token, base = (ctx[k] for k in ('work', 'ROOT', 'token', 'base'))
    config, command, env = (ctx[k] for k in ('config', 'command', 'runtime_env'))
    server = ctx['server']
    dbpath = work/'data/metadata.sqlite'

    def sql(statement, params=()):
        with sqlite3.connect(dbpath, timeout=10) as db:
            return db.execute(statement, params).fetchall()

    def reset_rate():
        sql('DELETE FROM rate_limits')

    def cancel(session):
        return request('DELETE', '/api/v1/publish-sessions/'+session['id'], {})

    def invoke(mode, options=(), cfg=config):
        return subprocess.run(command+['--config', str(cfg), mode]+list(options),
                              cwd=root, env=env, capture_output=True, text=True, timeout=240)

    def expect_error(result, status, code, name):
        actual, headers, body = result
        check(actual == status and body['error']['code'] == code
              and headers.get('content-type', '').startswith('application/vnd.sputnik.registry.v1+json')
              and body['error'].get('request_id') == headers.get('x-request-id')
              and headers.get('cache-control') == 'no-store', name)

    def waiting(statement, params, predicate, label):
        for _ in range(200):
            rows = sql(statement, params)
            if predicate(rows):
                return rows
            time.sleep(.025)
        raise AssertionError(label)

    def custom_package(ref, ver, var, files, extra=None, modules=True):
        source = work/('custom-'+secrets.token_hex(6)); source.mkdir()
        for name, contents in files.items():
            path = source/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
        write_source_manifest(source, ref, ver, var,
            {'main.s': 'test.demo'} if modules else None, files, extra)
        artifact = source/'artifact.sputnikpkg'
        result = subprocess.run([str(ctx['SDK']), 'pkg', 'build', str(source), '--out', str(artifact), '--variant', var], capture_output=True, text=True)
        check(result.returncode == 0, 'build custom '+ref+'/'+ver+'/'+var+': '+result.stderr[-200:])
        return artifact.read_bytes()

    def validate(payload, ver, var='source', ref='test/demo'):
        declaration = dict(package_ref=ref, version=ver, variant=var, expected_size=len(payload), expected_sha256='sha256:'+hashlib.sha256(payload).hexdigest())
        result = request('POST', '/api/v1/publish-sessions', declaration)
        check(result[0] == 201, 'create validation fixture '+ver+'/'+var)
        session = result[2]; path = '/api/v1/publish-sessions/'+session['id']
        check(upload(session['id'], payload)[0] == 200, 'upload validation fixture '+ver+'/'+var)
        check(request('POST', path+'/commit', {})[0] == 202, 'commit validation fixture '+ver+'/'+var)
        run('worker-once')
        return request('GET', path)[2]

    try:
        # Baseline deliberately leaves a retry reservation after archive rejection.
        for (session_id,) in sql("SELECT id FROM sessions WHERE reserved=1 AND state IN ('created','uploaded')"):
            check(cancel({'id':session_id})[0] == 200, 'clear baseline retry reservation')
        reset_rate()
        # Parsers must never expose runtime exceptions on either backend.
        for expiry in ['not-a-date', '2026-02-30T12:00:00Z', '2026-10-21', '0000-01-01T00:00:00Z', '2027-01-01T25:00:00Z', '2027-01-01T12:00:00+24:00', 42, None, []]:
            expect_error(request('POST', '/api/v1/tokens', dict(label='invalid', scopes=['registry:read'], expires_at=expiry)),
                         400, 'INVALID_EXPIRY', 'expiry schema rejects '+repr(expiry))
        future = datetime.now(timezone.utc)+timedelta(hours=1)
        for expiry in [future.isoformat(timespec='seconds').replace('+00:00','Z'), future.isoformat().replace('+00:00','Z'), future.astimezone(timezone(timedelta(hours=3))).isoformat()]:
            result = request('POST', '/api/v1/tokens', dict(label='valid-expiry', scopes=['registry:read'], expires_at=expiry))
            check(result[0] == 201, 'RFC3339 expiry accepts UTC/fraction/offset '+expiry)
            request('DELETE', '/api/v1/tokens/'+result[2]['id'], {})
        for cursor in ['@@.x', 'A.x', 'a===.x', 'AB==.x', '.x', 'a.b.c']:
            expect_error(request('GET', base+'/index?cursor='+cursor), 400, 'INVALID_CURSOR', 'cursor encoding rejects '+cursor)

        # Credentials: secret-once replay, delegation, logout and ACL cleanup.
        key = secrets.token_hex(16)
        issued = request('POST', '/api/v1/tokens', dict(label='logout', scopes=['registry:read']), key=key)
        check(issued[0] == 201, 'read-only logout token issued')
        own = issued[2]
        expect_error(request('POST', '/api/v1/tokens', dict(label='logout', scopes=['registry:read']), key=key),
                     409, 'TOKEN_SECRET_ALREADY_ISSUED', 'token creation replay never reissues secret')
        me = request('GET', '/api/v1/me')[2]
        expect_error(request('DELETE', '/api/v1/tokens/'+me['token_id'], {}, auth=own['token']),
                     403, 'FORBIDDEN', 'read-only token cannot revoke another token')
        check(request('DELETE', '/api/v1/tokens/'+own['id'], {}, auth=own['token'])[0] == 200, 'self-revoke does not require manage scope')
        check(request('GET', '/api/v1/me', auth=own['token'])[0] == 401, 'self-revoked token immediately denied')
        idempotency = sql('SELECT response FROM idempotency WHERE action=? AND key=?', ('issue-token', key))[0][0]
        check(own['token'] not in idempotency and own['id'] in idempotency, 'idempotency stores token id without secret')
        issuer = op('token-issue', account_id=ctx['account']['account_id'], label='limited', scopes=['registry:read', 'token:manage'])
        expect_error(request('POST', '/api/v1/tokens', dict(label='escalation', scopes=['package:publish:test/demo']), auth=issuer['token']),
                     403, 'SCOPE_ESCALATION', 'delegation cannot escalate scopes')
        manager = op('token-issue', account_id=ctx['account']['account_id'], label='acl', scopes=['registry:read', 'package:owners:test/demo', 'package:yank:test/demo'])['token']
        other = op('account-create', label='Suspended owner', namespace='suspended_owner')
        check(request('POST', base+'/owners', dict(account_id=other['account_id'], role='owner'), auth=manager)[0] == 200, 'add co-owner')
        op('account-suspend', account_id=other['account_id'])
        check(request('DELETE', base+'/owners/'+other['account_id'], {}, auth=manager)[0] == 200, 'remove suspended co-owner')
        expect_error(request('DELETE', base+'/owners/'+ctx['account']['account_id'], {}, auth=manager),
                     409, 'LAST_OWNER', 'last active owner removal rolls back')
        expect_error(request('POST', base+'/owners', dict(account_id=ctx['account']['account_id'], role='publisher'), auth=manager),
                     409, 'LAST_OWNER', 'last active owner demotion rolls back')

        # A session remains bound to its initiating credential after rotation.
        initiator = op('token-issue', account_id=ctx['account']['account_id'], label='initiator', scopes=me['scopes'])
        payload = ctx['payload']
        def initiated(ver):
            declaration = dict(package_ref='test/demo', version=ver, variant='source', expected_size=len(payload),
                               expected_sha256='sha256:'+hashlib.sha256(payload).hexdigest())
            result = request('POST', '/api/v1/publish-sessions', declaration, auth=initiator['token'])
            check(result[0] == 201, 'initiating token creates '+ver)
            return result[2]
        before_put = initiated('10.0.0')
        before_commit = initiated('10.0.1')
        check(upload(before_commit['id'], payload)[0] == 200, 'replacement token may upload while initiator remains active')
        request('DELETE', '/api/v1/tokens/'+initiator['id'], {})
        traffic_before = sql('SELECT count(*),coalesce(sum(bytes),0) FROM traffic WHERE account_id=?',(ctx['account']['account_id'],))
        expect_error(upload(before_put['id'], payload), 401, 'UNAUTHORIZED', 'revoked initiator blocks PUT admission')
        expect_error(request('POST', '/api/v1/publish-sessions/'+before_commit['id']+'/commit', {}),
                     401, 'UNAUTHORIZED', 'revoked initiator blocks commit admission')
        check(sql('SELECT count(*),coalesce(sum(bytes),0) FROM traffic WHERE account_id=?',(ctx['account']['account_id'],)) == traffic_before, 'denied initiating credentials consume no upload traffic')
        cancel(before_put); cancel(before_commit)
        initiator = op('token-issue', account_id=ctx['account']['account_id'], label='expiry', scopes=me['scopes'])
        expired = initiated('10.0.2')
        sql('UPDATE tokens SET expires_at=? WHERE id=?', (int(time.time())-1, initiator['id']))
        expect_error(upload(expired['id'], payload), 401, 'UNAUTHORIZED', 'expired initiator blocks upload admission')
        cancel(expired)
        initiator = op('token-issue', account_id=ctx['account']['account_id'], label='finalize', scopes=me['scopes'])
        streamed = initiated('10.0.3')
        sock = socket.create_connection(('127.0.0.1',ctx['port']),timeout=10)
        stream_path = '/api/v1/publish-sessions/'+streamed['id']
        sock.sendall(('PUT '+stream_path+'/artifact HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer '+token+
            '\r\nContent-Type: application/vnd.sputnik.package.v1\r\nContent-Length: '+str(len(payload))+'\r\n\r\n').encode()+payload[:1])
        waiting('SELECT state FROM sessions WHERE id=?',(streamed['id'],),lambda rows: rows[0][0]=='receiving','stream admission')
        request('DELETE','/api/v1/tokens/'+initiator['id'],{})
        sock.sendall(payload[1:])
        response = http.client.HTTPResponse(sock); response.begin(); response.read()
        check(response.status == 401 and request('GET',stream_path)[2]['state'] == 'created', 'initiator revoke during stream fences upload finalize')
        response.close(); sock.close(); cancel(streamed)
        initiator = op('token-issue', account_id=ctx['account']['account_id'], label='worker-revoke', scopes=me['scopes'])
        valid = fixture(ver='10.0.4')
        declaration = dict(package_ref='test/demo', version='10.0.4', variant='source', expected_size=len(valid), expected_sha256='sha256:'+hashlib.sha256(valid).hexdigest())
        committed = request('POST','/api/v1/publish-sessions',declaration,auth=initiator['token'])[2]
        upload(committed['id'],valid); path='/api/v1/publish-sessions/'+committed['id']
        request('POST',path+'/commit',{}); request('DELETE','/api/v1/tokens/'+initiator['id'],{}); run('worker-once')
        check(request('GET',path)[2].get('reason_code') == 'UNAUTHORIZED', 'revocation after commit fences final worker publication')

        # The service and SDK must accept the same variant grammar.
        reset_rate()
        for var in ['cpu.v1', 'a'*96, 'a'*97, 'a'*128]:
            result = create(payload, ver='10.1.0', var=var)
            check(result[0] == 201, 'accept variant '+var[:12]+'/'+str(len(var)))
            cancel(result[2])
        for var in ['a'*129, '_cpu', '-cpu', '.', '..', 'a..b', 'a/b']:
            expect_error(create(payload, ver='10.1.0', var=var), 400, 'INVALID_VARIANT', 'reject variant '+var[:12])
        dotted = fixture(ver='10.1.1', var='cpu.v1', extra={'target': {'os': 'macos', 'arch': 'aarch64', 'cpu_features': ['neon'], 'compute_backends': ['cpu']}})
        publish(dotted, ver='10.1.1', var='cpu.v1')
        exact = request('GET', base+'/versions/10.1.1/artifacts/cpu.v1')[2]
        compact = request('GET', base+'/versions/10.1.1')[2]['artifacts'][0]
        check(exact['target']['cpu_features'] == ['neon'] and exact['target']['compute_backends'] == ['cpu'], 'verified CPU/backend requirements survive facade')
        check(exact['compatibility']['sputnik_requirement'] == '>=1.0.0 <2.0.0' and exact['compatibility']['bytecode_versions'] == [] and exact['compatibility']['sputnik_versions'] == [], 'source compatibility schema')
        check(compact['compatibility'] == exact['compatibility'] and compact['bundle'] == exact['bundle'], 'compact metadata retains selection facts')
        check('archive_manifest_toml' not in exact and 'archive_manifest_json' not in exact, 'public metadata avoids duplicated archive manifest')
        bytecode = request('GET', base+'/versions/1.0.0/artifacts/bytecode')[2]
        with tarfile.open(work/'1.0.0-bytecode.sputnikpkg') as archive:
            build = json.load(archive.extractfile('meta/build.json'))
        facts = list(build['bytecode'].values())
        check(bytecode['compatibility']['bytecode_versions'] == [facts[0]['format']]
              and bytecode['compatibility']['profiles'] == [facts[0]['profile_flags']]
              and facts[0]['abi_hash'] in bytecode['compatibility']['bytecode_abi_hashes'], 'bytecode compatibility matches inspected build facts')

        # Native source/prebuilt and native-only providers use the same wire schema.
        reset_rate()
        arch = 'aarch64' if platform.machine() in ('arm64','aarch64') else 'x86_64'
        mac = platform.system() == 'Darwin'
        target = {'target': {'os': 'macos' if mac else 'linux', 'arch': arch}}
        if mac:
            target['target']['min_os'] = '13.0'
        else:
            target['target']['libc'] = 'musl' if platform.libc_ver()[0] == 'musl' else 'gnu'
        native_files = {'main.s':'package test.demo\nexport answer\ndef answer(): 42\n', 'answer.c':'int demo_answer(void) { return 42; }\n'}
        native_source = custom_package('test/demo', '10.1.2', 'native-source', native_files,
            {**target, 'native': [{'name': 'demo_native', 'language': 'c', 'sources': ['answer.c']}]})
        check(validate(native_source, '10.1.2', 'native-source').get('reason_code') == 'NATIVE_GRANT_REQUIRED', 'native payload requires explicit admission grant')
        op('grant', account_id=ctx['account']['account_id'], kind='native', target='test/demo')
        check(validate(native_source, '10.1.2', 'native-source')['state'] == 'published', 'granted native source publishes')
        native = request('GET', base+'/versions/10.1.2/artifacts/native-source')[2]
        check(native['native_mode'] == 'source' and native['compatibility']['sputnik_versions'] == ['1.0.0'], 'native-source compatibility carries shared language version')
        # Only host compiler builds the test library. Registry inspection never loads it.
        native_dir = work/'native-regression'; native_dir.mkdir()
        (native_dir/'answer.c').write_text(native_files['answer.c'])
        library = 'libdemo.dylib' if mac else 'libdemo.so'
        flags = ['-dynamiclib', '-mmacosx-version-min=13.0'] if mac else ['-shared', '-fPIC']
        compiler = subprocess.run(['clang']+flags+[str(native_dir/'answer.c'),'-o',str(native_dir/library)], capture_output=True, text=True)
        check(compiler.returncode == 0, 'build real prebuilt regression library: '+compiler.stderr[-400:])
        prebuilt = custom_package('test/demo', '10.1.3', 'prebuilt', {'main.s':native_files['main.s'], library:(native_dir/library).read_bytes()},
            {**target, 'native': [dict(name='demo_native',kind='prebuilt',library=library,sputnik_version='1.0.0')]})
        check(validate(prebuilt, '10.1.3', 'prebuilt')['state'] == 'published', 'prebuilt library publishes without loading')
        native = request('GET', base+'/versions/10.1.3/artifacts/prebuilt')[2]
        check(native['native_mode'] == 'prebuilt' and native['compatibility']['sputnik_versions'] == ['1.0.0'], 'prebuilt wire compatibility schema')
        provider_extra = {**target, 'native_providers': [dict(id='fixture-runtime',kind='runtime',version='1.0.0',abi='fixture-1',build_id='sha256:'+'a'*64,files=['data/runtime.bin'],library_dirs=['data'])]}
        provider = custom_package('test/provider', '1.0.0', 'runtime', {'data/runtime.bin':'runtime resources'}, provider_extra, modules=False)
        op('grant', account_id=ctx['account']['account_id'], kind='native', target='test/provider')
        check(validate(provider, '1.0.0', 'runtime', ref='test/provider')['state'] == 'published', 'native-only provider publishes without dummy module')
        provider_base = '/api/v1/namespaces/test/packages/provider'
        provider_exact = request('GET', provider_base+'/versions/1.0.0/artifacts/runtime')[2]
        provider_compact = request('GET', provider_base+'/index')[2]['versions'][0]['artifacts'][0]
        check(provider_exact['kind'] == 'native' and provider_compact['compatibility'] == provider_exact['compatibility'], 'native provider exact/index schema retains kind and compatibility')

        # Real simultaneous HTTP requests exercise uniqueness and idempotency.
        reset_rate()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(payload, ver='10.2.0'), range(2)))
        check(sorted(r[0] for r in results) == [201, 409], 'concurrent create has exactly one reservation')
        cancel(next(r[2] for r in results if r[0] == 201))
        key = secrets.token_hex(16)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(payload, ver='10.2.1', key=key), range(2)))
        check(all(r[0] == 201 for r in results) and results[0][2]['id'] == results[1][2]['id'], 'concurrent idempotency returns one session')
        cancel(results[0][2])

        # Kill web during a replay PUT, then reclaim the real persisted lease.
        retry_payload = fixture(ver='10.3.0')
        session = create(retry_payload, ver='10.3.0')[2]
        session_path = '/api/v1/publish-sessions/'+session['id']
        check(upload(session['id'], retry_payload)[0] == 200, 'durable upload before retry crash')
        prior = sql('SELECT upload_key FROM sessions WHERE id=?', (session['id'],))[0][0]
        sock = socket.create_connection(('127.0.0.1', ctx['port']), timeout=10)
        sock.sendall(('PUT '+session_path+'/artifact HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer '+token+
                      '\r\nContent-Type: application/vnd.sputnik.package.v1\r\nContent-Length: '+str(len(retry_payload))+'\r\n\r\n').encode()+retry_payload[:1])
        waiting('SELECT state FROM sessions WHERE id=?', (session['id'],), lambda rows: rows[0][0] == 'receiving', 'retry lease admission')
        expect_error(upload(session['id'], retry_payload), 409, 'UPLOAD_IN_PROGRESS', 'concurrent replay PUT is fenced')
        server.kill(); server.wait(timeout=10); sock.close()
        sql('UPDATE sessions SET receiving_until=? WHERE id=?', (int(time.time())-1, session['id']))
        server = ctx['start']()
        check(request('GET', session_path)[2]['state'] == 'uploaded'
              and sql('SELECT upload_key FROM sessions WHERE id=?', (session['id'],))[0][0] == prior, 'crashed replay recovers original uploaded bytes')
        check(request('POST', session_path+'/commit', {})[0] == 202, 'recovered upload commits without reupload')
        run('worker-once')
        check(request('GET', session_path)[2]['state'] == 'published', 'recovered replay publishes')

        # A durable CAS link with a lost worker process is safe to promote again.
        recovery_payload=fixture(ver='10.3.1')
        recovery=create(recovery_payload,ver='10.3.1')[2]; recovery_path='/api/v1/publish-sessions/'+recovery['id']
        upload(recovery['id'],recovery_payload); request('POST',recovery_path+'/commit',{})
        key,digest=sql('SELECT upload_key,digest FROM sessions WHERE id=?',(recovery['id'],))[0]
        destination=work/'data/blobs'/digest[:2]/digest; destination.parent.mkdir(exist_ok=True)
        os.link(work/'data/staging'/key,destination); destination.chmod(0o400)
        with destination.open('rb') as file: os.fsync(file.fileno())
        sql("UPDATE sessions SET state='promoting',worker_fence=?,lease_until=? WHERE id=?",(secrets.token_hex(16),int(time.time())+120,recovery['id']))
        check(run('worker-once')['processed'] is False, 'live worker lease prevents stale takeover')
        sql('UPDATE sessions SET lease_until=? WHERE id=?',(int(time.time())-1,recovery['id']))
        run('worker-once'); run('worker-once')
        check(request('GET',recovery_path)[2]['state']=='published' and sql("SELECT count(*) FROM audit WHERE kind='artifact_published' AND version='10.3.1'")[0][0]==1,'expired promotion lease recovers existing CAS link exactly once')

        # Metadata larger than the client limit must be reduced or rejected.
        source = work/'large-regression'; source.mkdir()
        modules = []
        for index in range(1800):
            name = 'test.'+'x'*210+'.m'+str(index); filename = 'm'+str(index)+'.s'
            (source/filename).write_text('package '+name+'\nexport answer\ndef answer(): 42\n')
            modules.append((filename, name))
        write_source_manifest(source, 'test/demo', '10.4.0', 'source', dict(modules), [p for p,n in modules])
        artifact = work/'large-regression.sputnikpkg'
        built = subprocess.run([str(ctx['SDK']), 'pkg', 'build', str(source), '--out', str(artifact)], capture_output=True, text=True)
        check(built.returncode == 0, 'SDK builds large valid manifest')
        large = artifact.read_bytes()
        session = create(large, ver='10.4.0')[2]; path = '/api/v1/publish-sessions/'+session['id']
        upload(session['id'], large); request('POST', path+'/commit', {}); run('worker-once')
        state = request('GET', path)[2]
        check(state['state'] == 'published', 'large valid source remains publishable after metadata deduplication')
        metadata = request('GET', base+'/versions/10.4.0/artifacts/source')[2]
        check(len(json.dumps(metadata, separators=(',', ':')).encode()) <= 786432, 'exact metadata stays within wire budget')
        fetch = invoke('fetch',
            ['--origin', f'http://127.0.0.1:{ctx["port"]}', '--credentials', str(ctx['credentials']), '--ref', 'test/demo', '--version', '10.4.0', '--variant', 'source', '--out', str(work/'large-regression-fetched.sputnikpkg')])
        check(fetch.returncode == 0 and (work/'large-regression-fetched.sputnikpkg').read_bytes() == large, 'CLI fetch succeeds for formerly oversized metadata')
        # Increase path duplication while leaving the canonical build manifest
        # under 1 MiB and common declarations under 768 KiB.
        long_source = work/'oversized-regression'; long_source.mkdir()
        modules = []
        for index in range(1500):
            name = 'test.'+'x'*210+'.m'+str(index); filename = 'p'*190+str(index)+'.s'
            (long_source/filename).write_text('package '+name+'\nexport answer\ndef answer(): 42\n')
            modules.append((filename, name))
        write_source_manifest(long_source, 'test/demo', '10.4.1', 'source', dict(modules), [p for p,n in modules])
        oversized = work/'oversized-regression.sputnikpkg'
        result = subprocess.run([str(ctx['SDK']), 'pkg', 'build', str(long_source), '--out', str(oversized)], capture_output=True, text=True)
        check(result.returncode == 0, 'SDK builds artifact with oversized exact descriptor: '+result.stderr[-400:])
        check(validate(oversized.read_bytes(), '10.4.1').get('reason_code') == 'METADATA_LIMIT', 'worker rejects oversized final descriptor before publication')

        # Fault and quota boundaries use SQL setup, with restoration after each.
        reset_rate()
        sessions = [create(payload, ver='11.0.'+str(i))[2] for i in range(3)]
        expect_error(create(payload, ver='11.0.4'), 429, 'SESSION_LIMIT', 'account active-session quota')
        for pending in sessions: cancel(pending)
        session = create(payload, ver='11.1.0')[2]
        sql('UPDATE sessions SET expires_at=? WHERE id=?', (int(time.time())-1, session['id']))
        check(request('GET', '/api/v1/publish-sessions/'+session['id'])[2]['state'] == 'expired', 'session expiry releases reservation')
        session = create(payload, ver='11.1.0')[2]; cancel(session)
        account_id = ctx['account']['account_id']
        traffic_id = secrets.token_hex(16)
        sql('INSERT INTO traffic VALUES(?,?,?,?,?)', (traffic_id, account_id, 'standard', 1073741824, int(time.time())))
        session = create(payload, ver='11.2.0')[2]
        expect_error(upload(session['id'], payload), 429, 'TRAFFIC_QUOTA', 'rolling traffic quota enforced before upload')
        sql('DELETE FROM traffic WHERE id=?', (traffic_id,)); cancel(session)

        # Identity/prefix/common metadata failures cannot publish or reserve forever.
        reset_rate()
        for version, expected in [('12.0.0', 'ARTIFACT_IDENTITY_MISMATCH')]:
            session = create(payload, ver=version)[2]; path = '/api/v1/publish-sessions/'+session['id']
            upload(session['id'], payload); request('POST', path+'/commit', {}); run('worker-once')
            result = request('GET', path)[2]
            check(result['state'] == 'rejected' and result['reason_code'] == expected, 'worker rejects wrong archive identity')
            retry = create(payload, ver=version); check(retry[0] == 201, 'rejection frees variant reservation'); cancel(retry[2])
        conflict = fixture(ver='1.0.0', var='conflict', extra={'dependencies': {'test/base': '1.0.0'}})
        session = create(conflict, var='conflict')[2]; path = '/api/v1/publish-sessions/'+session['id']
        upload(session['id'], conflict); request('POST', path+'/commit', {}); run('worker-once')
        check(request('GET', path)[2].get('reason_code') == 'RELEASE_METADATA_CONFLICT', 'variant cannot change common release declarations')

        # Release/variant quarantine and yank paths, including conditional HEAD.
        url = base+'/versions/1.0.0/artifacts/source/download'
        etag = request('HEAD', url)[1]['etag']
        check(request('POST', base+'/versions/1.0.0/yank', {'reason':'regression'}, auth=manager)[0] == 200, 'owner can yank')
        check(request('GET', url, raw=True)[0] == 200, 'exact yanked artifact remains downloadable')
        check(request('POST', base+'/versions/1.0.0/unyank', {}, auth=manager)[0] == 200, 'owner can unyank')
        op('quarantine', package_ref='test/demo', version='1.0.0', enabled=True)
        check(request('HEAD', url, headers={'If-None-Match':etag})[0] == 423, 'release quarantine precedes conditional HEAD')
        op('quarantine', package_ref='test/demo', version='1.0.0', enabled=False)

        # Corruption must be detected before 200/304/HEAD and without usage events.
        run('worker-once')
        before = request('GET', base+'/stats/downloads')[2]
        before_count = sum(row['count'] for row in before['rows'])
        digest = hashlib.sha256(payload).hexdigest(); blob = work/'data/blobs'/digest[:2]/digest
        original = blob.read_bytes(); blob.chmod(0o600)
        for changed in [b'x'*len(original), original[:-1]]:
            blob.write_bytes(changed)
            expect_error(request('GET', url), 503, 'STORAGE_UNAVAILABLE', 'corrupt CAS GET denied')
            check(request('HEAD', url)[0] == 503, 'corrupt CAS HEAD denied')
            expect_error(request('GET', url, headers={'If-None-Match':etag}), 503, 'STORAGE_UNAVAILABLE', 'corrupt CAS conditional GET denied')
        blob.unlink()
        expect_error(request('GET', url), 503, 'STORAGE_UNAVAILABLE', 'missing CAS denied')
        blob.write_bytes(original); blob.chmod(0o400)
        run('worker-once')
        after = request('GET', base+'/stats/downloads')[2]
        check(sum(row['count'] for row in after['rows']) == before_count, 'storage failures never increase successful download count')

        # Restore must validate referenced blobs and be usable over real HTTP.
        backup = work/'regression-backup'; run('backup', options=['--out', str(backup)])
        original_manifest = (backup/'backup.json').read_text(); manifest = json.loads(original_manifest)
        member = next(name for name in manifest['files'] if name.startswith('blobs/'))
        settings = json.loads(config.read_text())
        saved_metadata = (backup/'metadata.sqlite').read_bytes()
        for case in ['inventory', 'missing', 'digest', 'size', 'metadata-size']:
            trial = work/('restore-invalid-'+case)
            cfg = work/('restore-invalid-'+case+'.json'); settings['data_dir'] = str(trial); cfg.write_text(json.dumps(settings))
            body = json.loads(original_manifest); saved = (backup/member).read_bytes()
            if case == 'inventory': del body['files'][member]
            if case == 'missing': (backup/member).unlink()
            if case == 'digest': (backup/member).write_bytes(b'x'*len(saved))
            if case == 'size': (backup/member).write_bytes(saved[:-1])
            if case == 'metadata-size':
                with sqlite3.connect(backup/'metadata.sqlite') as db:
                    db.execute('UPDATE artifacts SET size=size+1 WHERE digest=?',(body['files'][member],))
                db.close()
                body['files']['metadata.sqlite'] = hashlib.sha256((backup/'metadata.sqlite').read_bytes()).hexdigest()
            (backup/'backup.json').write_text(json.dumps(body))
            result = invoke('restore', ['--backup', str(backup)], cfg=cfg)
            check(result.returncode != 0 and not trial.exists(), 'restore rejects '+case+' corruption before installing data_dir')
            (backup/member).write_bytes(saved); (backup/'metadata.sqlite').write_bytes(saved_metadata); (backup/'backup.json').write_text(original_manifest)
        restored = work/'regression-restored'; cfg = work/'regression-restored.json'
        settings['data_dir'] = str(restored); cfg.write_text(json.dumps(settings))
        check(invoke('restore', ['--backup', str(backup)], cfg=cfg).returncode == 0, 'verified backup installs complete directory')
        # Same canonical origin requires stopping the original listener first.
        server.terminate(); server.wait(timeout=10)
        restored_server = subprocess.Popen(command+['--config', str(cfg), 'serve'], cwd=root, env=env, stdout=ctx['log'], stderr=ctx['log'])
        server = restored_server
        for _ in range(400):
            try:
                if request('GET', '/health/ready', auth=None)[0] == 200: break
            except OSError: pass
            time.sleep(.05)
        with sqlite3.connect(restored/'metadata.sqlite') as db:
            artifacts = db.execute('SELECT ref,version,variant,digest FROM artifacts').fetchall()
        for ref, version, variant, digest in artifacts:
            location = '/api/v1/namespaces/'+ref.split('/')[0]+'/packages/'+ref.split('/')[1] if '/' in ref else '/api/v1/packages/'+ref
            status, _, bytes_ = request('GET', location+'/versions/'+version+'/artifacts/'+variant+'/download', raw=True)
            check(status == 200 and hashlib.sha256(bytes_).hexdigest() == digest, 'restored download '+ref+'/'+version+'/'+variant)
        server.terminate(); server.wait(timeout=10); server = ctx['start']()

        # Projection replays publication outbox and unacknowledged spool once.
        run('worker-once')
        usage_path = work/'data/usage.sqlite'
        with sqlite3.connect(usage_path) as db:
            before_events = db.execute('SELECT count(*) FROM events').fetchone()[0]
            row = db.execute("SELECT id,kind,ref,version,variant,size,complete,created_at FROM events WHERE kind='download' LIMIT 1").fetchone()
        sql('UPDATE outbox SET delivered=0')
        event = dict(zip(['id','kind','package_ref','version','variant','size','complete','created_at'], row))
        (work/'data/spool/batch').write_text(json.dumps(event)+'\n')
        run('worker-once')
        with sqlite3.connect(usage_path) as db:
            check(db.execute('SELECT count(*) FROM events').fetchone()[0] == before_events, 'outbox and acknowledged-event crash replays deduplicate')
        check(not (work/'data/spool/batch').exists(), 'projection acknowledges durable replay batch')
        reset_rate()
        return server
    except BaseException:
        if server.poll() is None:
            server.terminate(); server.wait(timeout=10)
        raise
