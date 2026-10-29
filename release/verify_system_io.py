#!/usr/bin/env python3
"""Verify installed System IO variants against adversarial filesystem fixtures."""
import argparse,json,os,shutil,subprocess,tempfile
from pathlib import Path
exec(Path(__file__).with_name('prepare.py').read_text().split('p=argparse.ArgumentParser')[0])
cli=ROOT/'sputnik-lang/build/sputnik';repo=ROOT/'sputnik-lang/packages/system_io'
out=Path(__file__).resolve().parent/'out/system-io';reports=[]
for archive in sorted(out.glob('macos-aarch64-*/*.sputnikpkg')):
 variant=archive.parent.name;work=out/'verification'/variant
 if work.exists():shutil.rmtree(work)
 work.mkdir(parents=True)
 bindings=repo/'build/release-providers'
 env=dict(os.environ,SPUTNIK_PKG_HOME=str(work/'home'),SPUTNIK_NATIVE_EXT_CACHE=str(work/'cache'),SPUTNIK_NATIVE_PROVIDERS=str(bindings/'runtime')+':'+str(bindings/'sdk'),SYSTEM_IO_SELFTEST='working')
 def call(label,cmd):
  with (work/(label+'.log')).open('w') as f:r=subprocess.run(list(map(str,cmd)),env=env,cwd=work,stdout=f,stderr=subprocess.STDOUT,timeout=600)
  if r.returncode:raise RuntimeError((work/(label+'.log')).read_text()[-18000:])
  print('PASS system-io',variant,label,flush=True)
 call('install',[cli,'pkg','install',archive,'--allow-native']+(['--build-from-source'] if '-source-' in variant else []))
 fixture=work/'fixture';fixture.mkdir()
 (fixture/'main.s').write_text('package tests.file\nexport value\ndef value(): 42\n')
 (fixture/'sputnik.build.yaml').write_text('schema: "sputnik.build.v1"\nname: "tests/file"\nversion: "1.0.0"\nsource_root: "."\nfiles:\n  - "main.s"\nmodule_names:\n  main.s: "tests.file"\n')
 def make_root(name):
  root=work/name;root.mkdir();outside=work/(name+'-outside');outside.mkdir();(outside/'secret').write_text('must survive');(root/'folder').mkdir();(root/'linked').symlink_to(outside,target_is_directory=True);(root/'symlink').symlink_to(outside/'secret')
  call(name+'-source-fixture',[cli,'pkg','build',fixture,'--out',root/'archive.sputnikpkg'])
  call(name+'-bytecode-fixture',[cli,'pkg','build',fixture,'--modules','bytecode','--out',root/'archive-bytecode.sputnikpkg'])
  return root,outside
 source=work/'check.s';source.write_text(rewrite((repo/'tests/selftest.s').read_text()))
 flags=[v for grant in ['ffi','fs.read','fs.write','fs.metadata'] for v in ['--grant',grant]]
 root,outside=make_root('vm-root');call('vm',[cli,'run',source]+flags+['--',root]);assert (outside/'secret').read_text()=='must survive'
 if variant.endswith('-source'):
  output=work/'check';call('native-build',[cli,'build',source,'--require-full-native','-o',output]+flags)
  root,outside=make_root('native-root');call('native',[output,root]);assert (outside/'secret').read_text()=='must survive'
 call('lock',[cli,'pkg','lock',source]);info=json.loads((archive.parent/'inspect.json').read_text());lock=(work/'sputnik.lock').read_text();assert variant in lock and info['sha256'] in lock
 reports.append(dict(variant=variant,sha256=info['sha256'],vm=True,lock=True,native=variant.endswith('-source')))
(out/'verification/result.json').write_text(json.dumps(reports,indent=2)+'\n')
