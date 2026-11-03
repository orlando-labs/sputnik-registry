#!/usr/bin/env python3
"""Explicit maintenance retirement of the exact pre-1.0 release snapshot.

Run with the registry web/worker stopped, a verified replacement matrix and a
native backup. Package ownership, aliases, accounts and audit history survive.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time
import uuid

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--data', type=Path, required=True)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--verified', type=Path, required=True)
p.add_argument('--receipt', type=Path, required=True)
a = p.parse_args()
before = json.loads(a.before.read_text())
verified = json.loads(a.verified.read_text())
assert len(verified) == 52 and all(r.get('public_download') for r in verified)
expected = {}
for r in verified:
    expected.setdefault((r['name'], r['version']), {})[r['variant']] = r['sha256']
assert len(expected) == 12
retired = [(ref, release['version']) for ref, index in before.items() for release in index['versions']]
assert not set(retired) & set(expected)
db = sqlite3.connect(str(a.data / 'metadata.sqlite'), timeout=30)
db.execute('PRAGMA foreign_keys=ON')
db.execute('BEGIN IMMEDIATE')
assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
assert not db.execute('SELECT 1 FROM sessions WHERE reserved=1 LIMIT 1').fetchone(), 'Publication still active'
for identity, variants in expected.items():
    actual = {variant: 'sha256:' + digest for variant, digest in db.execute('SELECT variant,digest FROM artifacts WHERE ref=? AND version=? AND quarantined=0', identity)}
    assert actual == variants, (identity, 'replacement matrix/hash differs')
old = []
for ref, version in retired:
    rows = db.execute('SELECT variant,digest,size FROM artifacts WHERE ref=? AND version=?', (ref, version)).fetchall()
    assert rows, (ref, version, 'snapshot changed')
    old.extend(dict(name=ref, version=version, variant=v, sha256=d, size=s) for v, d, s in rows)
assert len(old) == 55
for ref, version in retired:
    db.execute('DELETE FROM artifacts WHERE ref=? AND version=?', (ref, version))
    db.execute('DELETE FROM releases WHERE ref=? AND version=?', (ref, version))
    db.execute('UPDATE packages SET revision=revision+1 WHERE ref=?', (ref,))
    db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)',
        (uuid.uuid4().hex, 'legacy_release_retired', None, ref, version, None, 0, int(time.time())))
db.execute('UPDATE catalog SET revision=revision+1 WHERE id=1')
assert not db.execute('PRAGMA foreign_key_check').fetchall()
unreferenced = [digest for digest in sorted({r['sha256'] for r in old})
                if not db.execute('SELECT 1 FROM artifacts WHERE digest=? LIMIT 1', (digest,)).fetchone()]
db.commit()
deleted_bytes = 0
for digest in unreferenced:
    raw = digest.removeprefix('sha256:')
    assert len(raw) == 64 and all(c in '0123456789abcdef' for c in raw)
    path = a.data / 'blobs' / raw[:2] / raw
    if path.exists():
        deleted_bytes += path.stat().st_size
        path.unlink()
report = dict(retired_releases=len(retired), retired_artifacts=len(old),
              deleted_blobs=len(unreferenced), deleted_bytes=deleted_bytes,
              remaining_releases=db.execute('SELECT count(*) FROM releases').fetchone()[0],
              remaining_artifacts=db.execute('SELECT count(*) FROM artifacts').fetchone()[0],
              quick_check=db.execute('PRAGMA quick_check').fetchone()[0], retired=old)
a.receipt.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'retired'}, indent=2))
