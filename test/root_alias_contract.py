"""Operator root aliases retain canonical ownership, archives and revisions."""
import json
import os
import subprocess
import sqlite3


def run_root_alias_contract(t):
    ref = 'test/root_contract'
    payload = t.artifact('1.0.0', ref=ref)
    t.check(t.publish(payload, '1.0.0', ref=ref)['state'] == 'published', 'root fixture publishes scoped')
    missing = t.request('GET', '/api/v1/packages/root_contract')
    t.error(missing, 404, 'NOT_FOUND', 'unpromoted short name returns package-not-found')
    t.check(missing[2]['error']['suggestions'] == [ref], 'short name suggests exact scoped package')
    absent = t.request('GET', '/api/v1/packages/missing_root_contract')
    t.check(absent[2]['error']['suggestions'] == [], 'missing short name has no suggestions')
    result = t.invoke('promote', ['--ref', ref])
    t.check(result.returncode == 0 and json.loads(result.stdout) == dict(root_name='root_contract', package_ref=ref), 'operator console promotes published package')
    t.check(t.op('package-promote', package_ref=ref)['package_ref'] == ref, 'promotion is idempotent')
    t.check(t.sql("SELECT count(*) FROM audit WHERE kind='root_alias_created' AND ref=?", (ref,))[0][0] == 1, 'promotion audits once')
    root = '/api/v1/packages/root_contract'
    canonical = '/api/v1/namespaces/test/packages/root_contract'
    t.check(t.request('GET', root)[2]['package_ref'] == ref, 'root resolves canonical identity')
    t.check(t.request('GET', root)[2]['requested_ref'] == 'root_contract', 'resolution records requested alias')
    for tail in ['/index', '/versions/1.0.0', '/versions/1.0.0/artifacts/source']:
        t.check(t.request('GET', root + tail)[2] == t.request('GET', canonical + tail)[2], 'root shares canonical metadata ' + tail)
    t.check(t.request('GET', root + '/versions/1.0.0/artifacts/source/download', raw=True)[2] == payload, 'root downloads unchanged archive')
    t.check(t.request('HEAD', root + '/versions/1.0.0/artifacts/source/download')[0] == 200, 'root supports HEAD')
    t.error(t.create(payload, '2.0.0', ref='root_contract'), 409, 'ROOT_ALIAS_READ_ONLY', 'root alias cannot be published directly')
    t.error(t.request('POST', root + '/versions/1.0.0/yank', {}), 409, 'ROOT_ALIAS_READ_ONLY', 'root alias cannot mutate scoped releases')
    for missing in ['root_contract', 'test/missing_root']:
        t.check(t.invoke('promote', ['--ref', missing]).returncode != 0, 'promotion rejects ' + missing)
    other_ref = 'other/root_contract'
    other_payload = t.artifact('1.0.0', ref=other_ref)
    t.check(t.publish(other_payload, '1.0.0', ref=other_ref, auth=t.ctx['other']['token'])['state'] == 'published', 'second namespace may use same local name')
    t.check(t.invoke('promote', ['--ref', other_ref]).returncode != 0, 'root name cannot be reassigned')
    t.op('grant', account_id=t.ctx['account']['account_id'], kind='unscoped', target='root_granted')
    reserved_ref = 'test/root_granted'
    t.check(t.publish(t.artifact('1.0.0', ref=reserved_ref), '1.0.0', ref=reserved_ref)['state'] == 'published', 'grant collision fixture publishes scoped')
    t.check(t.invoke('promote', ['--ref', reserved_ref]).returncode != 0, 'exact unscoped grant reserves root name')
    snapshot = t.work/'root-alias-backup'
    t.run('backup', options=['--out', str(snapshot)])
    config = json.loads(t.config.read_text()); config['data_dir'] = str(t.work/'root-alias-restored')
    restored_config = t.work/'root-alias-restore.json'; restored_config.write_text(json.dumps(config))
    restored = t.invoke('restore', ['--backup', str(snapshot)], config=restored_config)
    t.check(restored.returncode == 0, 'alias backup restores')
    with sqlite3.connect(t.work/'root-alias-restored/metadata.sqlite') as db:
        t.check(db.execute('SELECT ref FROM root_aliases WHERE name=?', ('root_contract',)).fetchone() == (ref,), 'restored alias retains canonical target')
    # The SDK installs the alias as a canonical scoped package, not a second
    # identity. Then exercise module loading and exact dependency resolution.
    credentials = t.work / 'root-install-credentials.json'
    credentials.write_text(json.dumps(dict(origin=f'http://127.0.0.1:{t.port}', token=t.token)))
    credentials.chmod(0o600)
    fetched = t.work/'root-fetched.sputnikpkg'
    fetch = t.invoke('fetch', ['--origin', f'http://127.0.0.1:{t.port}', '--credentials', str(credentials),
        '--ref', 'root_contract', '--version', '1.0.0', '--variant', 'source', '--out', str(fetched)])
    t.check(fetch.returncode == 0 and fetched.read_bytes() == payload, 'registry CLI fetch resolves root alias')
    home = t.work / 'root-install-home'
    env = dict(os.environ, SPUTNIK_PKG_HOME=str(home))
    for name in ['root_contract', ref]:
        command = [str(t.ctx['SDK']), 'pkg', 'install', name, '--registry', f'http://127.0.0.1:{t.port}', '--credentials', str(credentials)]
        installed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        t.check(installed.returncode == 0, 'SDK installs ' + name + ': ' + installed.stderr[-1000:])
    t.check((home/ref/'1.0.0'/'source'/'test-root_contract-1.0.0.sputnikpkg').read_bytes() == payload, 'SDK retains original distribution bytes')
    app = t.work / 'root-install-app.s'
    app.write_text('package app\nfrom test.contract import answer\nio.stdout.write_line(answer().to_str)\n')
    ran = subprocess.run([str(t.ctx['SDK']), 'run', str(app)], env=env, capture_output=True, text=True, timeout=120)
    t.check(ran.returncode == 0 and ran.stdout.strip() == '42', 'SDK imports installed scoped source: ' + ran.stderr[-1000:])
