#!/usr/bin/env python3
"""Check each portable source/bytecode/mixed archive in a separate store."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--sputnik', type=Path, required=True)
p.add_argument('--platform', default='macos-aarch64')
a = p.parse_args()
out = a.out.resolve()
cli = a.sputnik.resolve()
names = ['progressbar', 'notebook-ui', 'sqlite3', 'plot', 'orm', 'orm-sqlite3', 'orbit', 'orbit-orm']
grants = {'orm': ['ffi'], 'orm-sqlite3': ['ffi'],
          'orbit': ['random.secure', 'net.listen', 'net.connect'],
          'orbit-orm': ['ffi', 'random.secure', 'net.listen', 'net.connect']}
reports = []
for name in names:
    for archive in sorted((out / name).glob('*/*.sputnikpkg')):
        variant = archive.parent.name
        if variant not in ['source', 'bytecode', 'mixed']:
            continue
        work = out / 'portable-verification' / name / variant
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        env = dict(os.environ, SPUTNIK_PKG_HOME=str(work / 'home'),
                   SPUTNIK_NATIVE_EXT_CACHE=str(out / 'portable-native-cache'))
        def run(label, cmd):
            with (work / (label + '.log')).open('w') as log:
                subprocess.run(list(map(str, cmd)), env=env, cwd=work,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        for dep in names:
            selected = archive if dep == name else out / dep / (
                a.platform + '-prebuilt-source' if dep in ['sqlite3', 'plot'] else 'source')
            if dep != name:
                selected = next(selected.glob('*.sputnikpkg'))
            run('install-' + dep, [cli, 'pkg', 'install', selected, '--allow-native'])
        case = 'orm' if name == 'orm-sqlite3' else name
        source = work / 'check.s'
        shutil.copy2(out / 'verification' / case / 'check.s', source)
        flags = [arg for grant in grants.get(name, []) for arg in ['--grant', grant]]
        run('vm', [cli, 'run', source, *flags])
        run('lock', [cli, 'pkg', 'lock', source])
        info = json.loads((archive.parent / 'inspect.json').read_text())
        lock = (work / 'sputnik.lock').read_text()
        assert info['sha256'] in lock and variant in lock, (name, variant)
        reports.append(dict(name=info['name'], version=info['version'], variant=variant,
                            sha256=info['sha256'], vm=True, lock=True))
        print('PASS portable', name, variant, flush=True)
(out / 'verified-portable.json').write_text(json.dumps(reports, indent=2) + '\n')
