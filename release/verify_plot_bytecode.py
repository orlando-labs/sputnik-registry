#!/usr/bin/env python3
"""Run Plot's complete conformance test through installed bytecode variants."""
import argparse,json,os,shutil,subprocess
from pathlib import Path
exec(Path(__file__).with_name('prepare.py').read_text().split('p=argparse.ArgumentParser')[0])
p=argparse.ArgumentParser();p.add_argument('--platform',default='macos-aarch64');a=p.parse_args()
out=Path(__file__).resolve().parent/'out/plot';cli=ROOT/'sputnik-lang/build/sputnik';reports=[]
for archive in sorted(out.glob(a.platform+'-*-bytecode/*.sputnikpkg')):
 variant=archive.parent.name;work=out/'verification'/variant
 if work.exists():shutil.rmtree(work)
 work.mkdir(parents=True)
 env=dict(os.environ,SPUTNIK_PKG_HOME=str(work/'home'),SPUTNIK_NATIVE_EXT_CACHE=str(work/'cache'))
 source=work/'check.s';text=rewrite((ROOT/'plot/test/selftest.s').read_text());text=text.replace('def main():','def _release_main():');text+='\ndef main():\n  score = _release_main()\n  raise ValueError("Plot conformance score") unless score == EXPECTED\n  print(score)\n  0\n';source.write_text(text)
 for label,cmd in [('install',[cli,'pkg','install',archive,'--allow-native']+(['--build-from-source'] if '-source-' in variant else [])),('vm',[cli,'run',source,'--grant','ffi']),('lock',[cli,'pkg','lock',source])]:
  with (work/(label+'.log')).open('w') as f:r=subprocess.run(list(map(str,cmd)),env=env,cwd=work,stdout=f,stderr=subprocess.STDOUT,timeout=300)
  if r.returncode:raise RuntimeError((work/(label+'.log')).read_text()[-12000:])
  print('PASS plot',variant,label,flush=True)
 info=json.loads((archive.parent/'inspect.json').read_text());lock=(work/'sputnik.lock').read_text();assert variant in lock and info['sha256'] in lock
 reports.append(dict(variant=variant,sha256=info['sha256'],vm=True,lock=True))
(out/'verification'/('result-'+a.platform+'.json')).write_text(json.dumps(reports,indent=2)+'\n')
