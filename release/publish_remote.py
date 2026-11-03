#!/usr/bin/env python3
"""Publish a checked remote archive with a temporary package-scoped token."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.request
import uuid

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--port', type=int, required=True)
p.add_argument('--sputnik', required=True)
p.add_argument('--archive', required=True)
p.add_argument('--environment', required=True)
p.add_argument('--receipt', type=Path, required=True)
a = p.parse_args()
origin = 'https://registry.sputnik-lang.online'
credential = Path.home() / '.sputnik/credentials' / (hashlib.sha256(origin.encode()).hexdigest() + '.json')
parent = json.loads(credential.read_text())
assert parent['origin'] == origin

def api(method, path, body):
    request = urllib.request.Request(origin + path, method=method,
        headers={'Authorization': 'Bearer ' + parent['token'],
                 'Content-Type': 'application/json', 'Idempotency-Key': uuid.uuid4().hex},
        data=json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

child = api('POST', '/api/v1/tokens', {
    'label': 'temporary-torch-release-20261031-' + str(a.port),
    'scopes': ['registry:read', 'package:publish:slowpilot/torch'],
    'expires_at': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ')})
remote = r'''
import json,os,subprocess,sys,tempfile
from pathlib import Path
payload=json.load(sys.stdin)
fd,path=tempfile.mkstemp(prefix='sputnik-release-token-',suffix='.json')
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'w') as f:json.dump({'origin':payload['origin'],'token':payload['token']},f)
 command='source '+__import__('shlex').quote(payload['environment'])+'; exec '+__import__('shlex').join([payload['sputnik'],'pkg','publish',payload['archive'],'--credentials',path,'--allow-native-large'])
 subprocess.run(['bash','-c',command],check=True)
finally:Path(path).unlink()
'''
receipt = {'port': a.port, 'archive': a.archive, 'delegated_token_id': child['id']}
try:
    payload = dict(origin=origin, token=child['token'], sputnik=a.sputnik,
                   archive=a.archive, environment=a.environment)
    subprocess.run(['ssh', '-p', str(a.port), 'lab.orlando-labs.com',
                    'python3.12 -c ' + shlex.quote(remote)],
                   input=json.dumps(payload).encode(), check=True)
    receipt['published'] = True
finally:
    api('DELETE', '/api/v1/tokens/' + child['id'], {})
    receipt['temporary_token_revoked'] = True
    a.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
print('Published remote archive; temporary token revoked:', a.port)
