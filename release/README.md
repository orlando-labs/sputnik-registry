# Registry release tools

`prepare.py` creates scoped archives from reviewed package sources. It rewrites
module references into the publisher namespace and builds native adapters at
`-O3`; Sputnik consumers retain their normal optimized compiler settings.
Source, bytecode and mixed delivery are independent of the source/prebuilt
native adapter choice. Macro provider modules stay as source, and redundant
mixed artifacts with no bytecode are omitted.

The individual package repositories contain their release configuration and a
copy of this tool. Set `SPUTNIK_ROOT` to the SDK checkout. Native releases are
built on their actual target; current verified targets are macOS arm64 >=26.0
and Linux x86-64 GNU >=2.36. Pure Sputnik variants are portable.

```sh
python3 release/prepare.py ../sqlite3/release/package.json --native source
python3 release/prepare.py ../sqlite3/release/package.json --native prebuilt
```

`verify.py` runs the original conformance fixtures from isolated installed
stores. `verify_foreign.py`, `verify_system_io.py` and
`verify_plot_bytecode.py` cover their native and bytecode matrices, including
exact lock hashes. `verify_public.py` compares every published small artifact
with the inspected producer hash and downloads it through the public hub.
Torch uses its own CMake/Sputnik release and actual CPU/MPS/CUDA tests.

Generation does not publish. Publishing requires the authenticated SDK CLI and
an explicit registry grant for each package containing native payloads. Local
runtime/SDK providers are not uploaded. Never commit provider bindings, token
files, publish sessions, archives, caches or generated build directories.
