#!/usr/bin/env python3
"""Run the actual bridge conformance tests from installed scoped archives."""
import argparse,json,os,re,shutil,subprocess,time
from pathlib import Path
exec(Path(__file__).with_name('prepare.py').read_text().split('p=argparse.ArgumentParser')[0])
p=argparse.ArgumentParser();p.add_argument('bridge',choices=['pycall','rbcall']);p.add_argument('--platform',default='macos-aarch64');p.add_argument('--python',default='/opt/homebrew/bin/python3.14');p.add_argument('--native',action='store_true');p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'out');p.add_argument('--sputnik',type=Path,default=SDK_ROOT/'build/sputnik');a=p.parse_args()
root=ROOT/a.bridge;out=a.out.resolve()/a.bridge
cli=a.sputnik.resolve();reports=[]
for archive in sorted(out.glob(a.platform+'-*/*.sputnikpkg')):
 variant=archive.parent.name
 work=out/'verification'/variant
 if work.exists():shutil.rmtree(work)
 work.mkdir(parents=True)
 if a.bridge=='rbcall':
  fixture=work/'rbcall/test/fixture.rb';fixture.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/'test/fixture.rb',fixture)
 bindings=root/'build/release-providers';env=dict(os.environ,SPUTNIK_PKG_HOME=str(work/'home'),SPUTNIK_NATIVE_PROVIDERS=str(bindings/'runtime')+':'+str(bindings/'sdk'),SPUTNIK_NATIVE_EXT_CACHE=str(work/'cache'))
 if a.bridge=='pycall':env['SPUTNIK_PYTHON_EXECUTABLE']=a.python
 def call(label,cmd,expected=None):
  with (work/(label+'.log')).open('w') as f:r=subprocess.run(list(map(str,cmd)),env=env,cwd=work,stdout=f,stderr=subprocess.STDOUT,timeout=600)
  log=(work/(label+'.log')).read_text()
  if expected:
   if r.returncode<=0 or expected not in log.lower():raise RuntimeError(log)
  elif r.returncode:raise RuntimeError(log[-10000:])
  print('PASS',a.bridge,variant,label,flush=True)
 call('install',[cli,'pkg','install',archive,'--allow-native']+(['--build-from-source'] if '-source-' in variant else []))
 for case in ['smoke','invalid_handle']:
  source=root/'test'/(case+'.s');consumer=work/(case+'.s');consumer.write_text(rewrite(source.read_text()))
  call(case+'-vm',[cli,'run',consumer,'--grant','ffi'], 'destroy' if case=='invalid_handle' else None)
  if a.native and variant.endswith('-source'):
   executable=work/(case+'-native');call(case+'-native-build',[cli,'build',consumer,'--require-full-native','-o',executable,'--grant','ffi']);call(case+'-native',[executable], 'destroy' if case=='invalid_handle' else None)
 call('lock',[cli,'pkg','lock',work/'smoke.s'])
 info=json.loads((archive.parent/'inspect.json').read_text());lock=(work/'sputnik.lock').read_text()
 if info['sha256'] not in lock or variant not in lock:raise RuntimeError('Lock does not pin exact variant/hash')
 reports.append(dict(variant=variant,sha256=info['sha256'],vm=True,lock=True))
(out/'verification/result.json').write_text(json.dumps(reports,indent=2)+'\n')
