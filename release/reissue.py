#!/usr/bin/env python3
"""Build a new language-compatible release of the existing registry matrix.

Publication and retirement are separate operations. Existing archives are never
rewritten; outputs and generated configurations use a new release directory.
"""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RELEASE = Path(__file__).resolve().parent
SPECS = {
    'progressbar': ROOT / 'sputnik-lang/packages/progressbar/release/package.json',
    'notebook-ui': ROOT / 'sputnik-lang/packages/notebook_ui/release/package.json',
    'sqlite3': ROOT / 'sqlite3/release/package.json',
    'plot': ROOT / 'plot/release/package.json',
    'orm': ROOT / 'orm/release/package.json',
    'orm-sqlite3': ROOT / 'orm/release/sqlite3.json',
    'orbit': ROOT / 'orbit/release/package.json',
    'orbit-orm': ROOT / 'orbit/release/orm.json',
}
VERSIONS = {name: '0.1.1' for name in
            ['progressbar', 'notebook-ui', 'sqlite3', 'plot', 'orm', 'orm-sqlite3',
             'pycall', 'rbcall', 'system-io']}
VERSIONS.update(orbit='1.0.0-rc.2', **{'orbit-orm': '1.0.0-rc.2', 'torch': '0.1.4'})

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--matrix', type=Path, required=True,
                    help='Saved public package indexes before the reissue')
parser.add_argument('--platform', choices=['macos-aarch64', 'linux-x86_64-gnu'], required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--sputnik', type=Path, default=ROOT / 'sputnik-lang/build/sputnik')
parser.add_argument('--python', default='/opt/homebrew/bin/python3.14' if sys.platform == 'darwin' else '/usr/bin/python3')
parser.add_argument('--ruby', default=shutil.which('ruby') or '/usr/bin/ruby')
args = parser.parse_args()
args.out = args.out.resolve()
args.out.mkdir(parents=True, exist_ok=True)
matrix = json.loads(args.matrix.read_text())


def run(label, command):
    print('RUN', label, flush=True)
    with (args.out / (label + '.log')).open('w') as log:
        result = subprocess.run(list(map(str, command)), stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(label + ' failed; inspect ' + str(args.out / (label + '.log')))


def configuration(name):
    if name in SPECS:
        original = SPECS[name]
    elif name in ('pycall', 'rbcall'):
        repo = ROOT / name
        interpreter = args.python if name == 'pycall' else args.ruby
        run(name + '-providers', [sys.executable, repo / 'release/providers.py',
            '--interpreter', interpreter, '--out', repo / 'build/release-providers'])
        original = repo / 'build/release-config.json'
    elif name == 'system-io':
        repo = ROOT / 'sputnik-lang/packages/system_io'
        manifest = repo / 'sputnik.build.yaml'
        previous = manifest.read_bytes()
        try:
            run('system-io-providers', [sys.executable, repo / 'tools/configure.py',
                '--sdk', os.environ.get('SPUTNIK_ROOT', str(ROOT / 'sputnik-lang'))])
        finally:
            manifest.write_bytes(previous)
        original = repo / 'build/release-config.json'
    else:
        raise ValueError('unknown package ' + name)
    cfg = json.loads(original.read_text())
    cfg['version'] = VERSIONS[name]
    cfg['requires'] = {'sputnik': '>=1.0.0 <2.0.0'}
    cfg['dependencies'] = {key: VERSIONS.get(key, value)
                           for key, value in cfg.get('dependencies', {}).items()}
    path = original.parent.parent / 'build' / ('reissue-' + name + '.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + '\n')
    return path


expected = []
for ref, index in matrix.items():
    name = ref.split('/')[1]
    if name == 'torch':
        continue
    current = next(release for release in index['versions'] if not release.get('yanked'))
    variants = [artifact['variant'] for artifact in current['artifacts']
                if artifact['variant'].startswith(args.platform + '-') or
                (args.platform == 'macos-aarch64' and artifact['variant'] in ('source', 'bytecode', 'mixed'))]
    if not variants:
        continue
    config = configuration(name)
    for variant in variants:
        native = 'prebuilt' if '-prebuilt-' in variant else 'source'
        modules = variant.rsplit('-', 1)[-1]
        run(name + '-' + variant, [sys.executable, RELEASE / 'prepare.py', config,
            '--sputnik', args.sputnik, '--out', args.out, '--platform', args.platform,
            '--native', native, '--modules', modules])
        archive = args.out / name / variant / (name + '-' + variant + '.sputnikpkg')
        if not archive.is_file():
            raise RuntimeError('existing variant was not reproduced: ' + variant)
        inspected = json.loads((archive.parent / 'inspect.json').read_text())
        if inspected['name'] != ref or inspected['version'] != VERSIONS[name]:
            raise RuntimeError('incorrect archive identity: ' + str(archive))
        expected.append(dict(name=ref, version=VERSIONS[name], variant=variant,
                             path=str(archive), sha256=inspected['sha256']))
(args.out / ('matrix-' + args.platform + '.json')).write_text(
    json.dumps(expected, indent=2) + '\n')
print('BUILT', len(expected), 'existing variants for', args.platform, flush=True)
