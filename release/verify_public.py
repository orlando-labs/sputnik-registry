#!/usr/bin/env python3
"""Compare public releases with verified archives and download every small artifact."""
import argparse,hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'out');a=p.parse_args();out=a.out.resolve()
work=out/'public-verification';work.mkdir(parents=True,exist_ok=True)
origin='https://registry.sputnik-lang.online';expected={}
for archive in sorted(out.glob('*/*/*.sputnikpkg')):
 info=json.loads((archive.parent/'inspect.json').read_text())
 expected.setdefault((info['name'],info['version']),{})[info['variant']]=info
def fetch(url,destination):
 subprocess.run(['curl','--fail','--silent','--show-error','--retry','3','--retry-all-errors','--http1.1',url,'-o',str(destination)],check=True,timeout=180)
reports=[]
for (ref,version),variants in sorted(expected.items()):
 slug,name=ref.split('/');base=f'/api/v1/namespaces/{slug}/packages/{name}/versions/{version}'
 path=work/(name+'-release.json');fetch(origin+base,path);release=json.loads(path.read_text())
 actual={a['variant']:a for a in release['artifacts'] if not a['quarantined']}
 assert set(actual)==set(variants),(ref,set(actual)^set(variants))
 alias=work/(name+'-alias.json');fetch(origin+'/api/v1/packages/'+name,alias);assert json.loads(alias.read_text())['package_ref']==ref
 for variant,info in variants.items():
  artifact=actual[variant];assert artifact['sha256']==info['sha256']
  downloaded=work/(name+'-'+variant+'.sputnikpkg');fetch(artifact['url'],downloaded)
  assert 'sha256:'+hashlib.sha256(downloaded.read_bytes()).hexdigest()==info['sha256']
  reports.append(dict(name=ref,version=version,variant=variant,sha256=info['sha256'],public_download=True,root_alias=True))
  print('PASS public',ref,variant,flush=True)
(work/'result.json').write_text(json.dumps(reports,indent=2)+'\n')
