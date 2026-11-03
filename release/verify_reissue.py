#!/usr/bin/env python3
"""Run installed-package conformance for a reproduced release matrix."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--platform', required=True)
p.add_argument('--sputnik', type=Path, required=True)
p.add_argument('--python', default=sys.executable)
a = p.parse_args()
release = Path(__file__).resolve().parent
out = a.out.resolve()
cli = a.sputnik.resolve()
results = []

def check(label, script, *options):
    cmd = [sys.executable, str(release / script), '--out', str(out),
           '--sputnik', str(cli), *options]
    print('CHECK', label, flush=True)
    with (out / ('verify-' + label + '.log')).open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    results.append(label)

for adapter in ('source', 'prebuilt'):
    check(adapter + '-vm', 'verify.py', '--platform', a.platform, '--adapter', adapter)
    for case in ('sqlite3', 'sqlite3-pool', 'sqlite3-backup', 'plot', 'orm', 'orm-pool'):
        check(adapter + '-native-' + case, 'verify.py', '--platform', a.platform,
              '--adapter', adapter, '--native', '--case', case)
check('plot-bytecode', 'verify_plot_bytecode.py', '--platform', a.platform)
for bridge in ('pycall', 'rbcall'):
    check(bridge, 'verify_foreign.py', bridge, '--platform', a.platform,
          '--python', a.python, '--native')
if a.platform == 'macos-aarch64':
    check('system-io', 'verify_system_io.py')
(out / ('verified-' + a.platform + '.json')).write_text(
    json.dumps({'platform': a.platform, 'checks': results}, indent=2) + '\n')
print('VERIFIED', a.platform, len(results), flush=True)
