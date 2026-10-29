"""YAML/JSON build-manifest fixtures shared by registry protocol tests."""
import json


def write_source_manifest(directory, name, version, variant, modules, files, extra=None, fields=None):
    data = dict(schema='sputnik.build.v1', name=name, version=version,
                source_root='.', files=list(files), artifact=dict(variant=variant))
    if modules:
        data['module_names'] = modules
    else:
        data['kind'] = 'native'

    def merge(destination, values):
        for key, value in (values or {}).items():
            if key == 'native': key = 'native_extensions'
            if key == 'build-dependencies': key = 'build_dependencies'
            if '.' in key and key.startswith('artifact.'):
                merge(destination.setdefault('artifact', {}), {key.split('.', 1)[1]: value})
            elif key == 'files' and isinstance(value, dict):
                destination.setdefault('files', []).extend(value.get('include', []))
            elif isinstance(value, dict) and isinstance(destination.get(key), dict):
                merge(destination[key], value)
            else:
                destination[key] = value
    merge(data, extra)
    merge(data['artifact'], fields)
    data['files'] = list(dict.fromkeys(data['files']))
    if data.get('native_extensions'):
        data['profiles'] = dict(required=['core.v1', 'ffi.v1'])
    (directory/'sputnik.build.yaml').write_text(''.join(k+': '+json.dumps(v)+'\n' for k,v in data.items()))
