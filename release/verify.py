#!/usr/bin/env python3
"""Exercise release archives in an isolated store, with no sibling module inputs."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path
import importlib.util
spec=importlib.util.spec_from_file_location('release_prepare',Path(__file__).with_name('scope.py'))
# Keep scope helpers importable without running the preparer's CLI.
exec(Path(__file__).with_name('prepare.py').read_text().split('p=argparse.ArgumentParser')[0])
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'out');p.add_argument('--sputnik',type=Path,default=ROOT/'sputnik-lang/build/sputnik');p.add_argument('--native',action='store_true');p.add_argument('--case');p.add_argument('--platform',default='macos-aarch64');p.add_argument('--adapter',choices=['source','prebuilt'],default='source');a=p.parse_args()
work=a.out.resolve()/'verification';work.mkdir(parents=True,exist_ok=True)
if not a.case: shutil.rmtree(work/'home',ignore_errors=True)
env=dict(os.environ,SPUTNIK_PKG_HOME=str(work/'home'),SPUTNIK_NATIVE_EXT_CACHE=str(work/'native-cache'))
cli=str(a.sputnik.resolve())
def invoke(cmd,log):
 print('+',' '.join(map(str,cmd)),flush=True)
 with log.open('w') as f:r=subprocess.run(list(map(str,cmd)),env=env,cwd=log.parent,stdout=f,stderr=subprocess.STDOUT)
 if r.returncode:print(log.read_text()[-14000:]);raise SystemExit(r.returncode)
# Install all dependencies before resolving test imports. Defaults prefer source modules.
for name in ['progressbar','notebook-ui','sqlite3','plot','orm','orm-sqlite3','orbit','orbit-orm']:
 if a.case:break
 variants=list((a.out.resolve()/name).glob('*/'+name+'-*.sputnikpkg'))
 for archive in variants:
  # Initial verification keeps source representation and current platform.
  if native_name := (name in ['sqlite3','plot']):
   if a.platform+'-'+a.adapter+'-source' not in str(archive):continue
  elif '/source/' not in str(archive):continue
  invoke([cli,'pkg','install',archive]+(['--allow-native','--build-from-source'] if a.adapter=='source' else ['--allow-native']),work/(name+'-install.log'))
cases={
 'progressbar':('sputnik-lang/packages/progressbar/tests/fixture.s',[]),
 'notebook-ui':(None,[]),
 'sqlite3':('sqlite3/test/selftest.s',['ffi']),
 'sqlite3-pool':('sqlite3/test/pool_selftest.s',['ffi']),
 'sqlite3-backup':('sqlite3/test/backup_selftest.s',['ffi']),
 'plot':('plot/test/selftest.s',['ffi']),
 'orm':('orm/test/selftest.s',['ffi']),
 'orm-pool':('orm/test/pool_selftest.s',['ffi']),
 'orbit':('orbit/test/core_selftest.s',['random.secure','net.listen','net.connect']),
 'orbit-contract':('orbit/test/contract_selftest.s',['random.secure','net.listen','net.connect']),
 'orbit-orm':('orbit/test/orm_selftest.s',['ffi','random.secure','net.listen','net.connect']),
}
for name,(source,grants) in cases.items():
 if a.case and name!=a.case:continue
 if a.native and name in ['progressbar','notebook-ui']:continue
 consumer=work/name;consumer.mkdir(exist_ok=True);path=consumer/'check.s'
 text=re.sub(r'^package [^\n]+\n','package releasecheck.'+name.replace('-','_')+'\n',rewrite((ROOT/source).read_text()) if source else '''package releasecheck.ui
from slowpilot.notebook.ui import DataTableView
export main
def main():
  view = DataTableView([{name: "Ada", score: 7}])
  raise ValueError("CSV export") unless view.to_csv().contains?("Ada")
  raise ValueError("Markdown export") unless view.to_markdown().contains?("Ada")
  0
''',flags=re.M)
 text=re.sub(r'^def main\(', 'def _release_main(',text,flags=re.M)
 expected=re.search(r'^EXPECTED = ([0-9]+)',text,re.M)
 text += '\ndef main():\n  release_result = _release_main()\n'
 if expected:text += '  raise ValueError("selftest score mismatch: " + release_result.to_str()) unless release_result == '+expected.group(1)+'\n'
 text += '  print("PASS '+name+' result=" + release_result.to_str())\n  0\n'
 path.write_text(text)
 flags=[arg for g in grants for arg in ['--grant',g]]
 if a.native:
  output=consumer/'check';invoke([cli,'build',path,'--require-full-native','-o',output]+flags,consumer/'native-build.log');invoke([output],consumer/'native.log')
 else:invoke([cli,'run',path]+flags,consumer/'vm.log')
 print('PASS',name,'native' if a.native else 'VM',flush=True)
