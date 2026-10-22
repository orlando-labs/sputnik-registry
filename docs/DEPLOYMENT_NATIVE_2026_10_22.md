# Native variants and CUDA deployment, 2026-10-22

The public hub is `https://registry.sputnik-lang.online`. Its web/worker run on
`.97`, store artifacts on `/md0/sputnik-registry/data`, and use the image built
on `.136`:

```text
docker.orlando-labs.com/sputnik-registry:20261022-4
sha256:529698af63657c5800297dbefa95a4dff863ba0e7b8e24e123a9f58f4f34a5f4
```

The image passed 772 full-native integration checks and both storage modes.
It retains `-O2`, 8 LLVM optimization workers, 16 partitions and ThinLTO;
all 1967 Sputnik functions use native execution. The adapter uses `-O3`.
The verified pre-update backup is
`/md0/sputnik-registry/backups/20261022-before-native-variants`.

Native-large is enabled, with separate exact `native` and `native-large`
grants for `slowpilot/sputniktorch`. No account-wide large grant was issued.
Admission checks the package reference throughout reservation, upload, commit
and promotion. nginx permits 2 GiB streaming requests; the standard profile
retains its 64 MiB limit. Worker memory remains capped at 512 MiB.

## Published CUDA package

`slowpilot/sputniktorch@0.1.2` is published and available through the root alias.
Before publication the completed archive was installed in a clean store, locked
with `--backend cuda`, compiled with `--require-full-native`, and run in VM and
native modes on both tested GPUs without a producer `LD_LIBRARY_PATH`.

| Field | Value |
|---|---|
| Package | `slowpilot/sputniktorch@0.1.2` |
| Root alias | `sputniktorch` |
| Variant | `linux-x86_64-gnu-cu130-prebuilt-source-runtime` |
| SHA-256 | `1c20ebd5c83e52ed0c1d71406710b5910a18ebeaadbff606a0d7d80cdc63c8ea` |
| Compressed bytes | 1,977,456,384 |
| Expanded bytes | 3,044,637,184 |
| Sputnik modules | 202 source modules |
| Technical dependency | exact LibTorch `2.14.0`, CUDA runtime `13.0` |
| Provider ABI | `libtorch-2.14.0-cxx11-linux-x86_64-gnu-cu130` |
| Provider build | `sha256:badc2b8b108ccb207c4ece3440eded3c2630c8d765a1f51245b20edb659305f7` |
| Platform | Linux x86_64, glibc >= 2.28 |
| NVIDIA driver | >= 580.105.08 (verified producer minimum) |
| Verified GPU capabilities | 8.6 (RTX 3090), 8.9 (RTX 4090) |

The package was built on `ssh -p 22176 lab.orlando-labs.com`, in
`/ssd1Tb/slowpilot-sputnik-cuda-20261022/packages-012`. LibTorch came from
the official C++ `2.14.0+cu130` distribution, pinned by archive SHA-256
`4f01b33fc0c9fa6684f7161d9807719d18fed6ef0d7d089c02ced01906d52605`.
Gloo is pinned to `74cc005ae13f69c11d8a41e50b42025b6730e796`.

The build requires no Python or host CUDA compiler. The full adapter is checked
for unresolved imports, including the official Linux provider's `onnx_torch`
namespace, rather than relying only on a LibTorch kernel probe. All bundled
runtime libraries, requirements and retained license materials are inventoried.
LibTorch 2.11 cannot satisfy the package's technical requirement.

Exact reports:

- [Public release metadata](evidence/2026-10-22/public-release.json)
- [Archive and technical dependency inventory](evidence/2026-10-22/sputniktorch-cuda-package.json)
- [Public consumer lock](evidence/2026-10-22/sputnik.lock)
- [CUDA VM result](evidence/2026-10-22/cuda-vm.txt), [full-native result](evidence/2026-10-22/cuda-native.txt)

## Installation and lock

On the GPU host reached through SSH port 22176, select a supported device by UUID. Its GTX 1080 Ti
is outside this CUDA 13 artifact's target. CUDA ordinals and `nvidia-smi` indices
are not interchangeable:

```sh
export CUDA_VISIBLE_DEVICES=GPU-f8d896e0-2607-a025-88a1-7ed8979035af
sputnik pkg install sputniktorch --version 0.1.2 \
  --backend cuda --bundle runtime --allow-native --allow-native-large
sputnik pkg lock app.s --backend cuda
sputnik pkg install slowpilot/sputniktorch --locked sputnik.lock \
  --allow-native --allow-native-large
```

For an explicit selection, replace `--backend cuda` with
`--variant linux-x86_64-gnu-cu130-prebuilt-source-runtime` and retain the bundle
and native opt-ins. The lock records the canonical scoped reference, version,
variant, exact archive hash and registry origin. Its archive hash binds the
technical version, ABI and provider build as well. Run/build never replace an
incompatible locked CUDA build with a CPU build.

The private OpenSSL build used by this host's remote core now points its default
`ssl/cert.pem` at the system CA bundle. TLS verification remains enabled.

SDK regression evidence includes 305 distribution checks, selection/store
checks, real HTTP native installation and lock restoration, native source RPATH
after atomic promotion, native loading in the notebook engine, and actual MPS
forward/backward with CPU fallback disabled. An activation reuses its verified
archive inventory to avoid decompressing the same CUDA archive twice.

## Notebook verification

The dependency editor now passes backend, bundle, variant and native/source/large
opt-ins to local and remote installers. Variant and archive digest are displayed
in inventory; removing one variant preserves the others. The real GUI/helper/SDK
integration test passes native opt-in rejection, wrong-variant rejection,
coexisting variants and independent removal. Its source is
`../sputnik-notebook/tests/notebook_dependencies_tests.swift`.

The core uses a literal-argument launcher for cwd support on glibc 2.28.
VM/full-native tests cover spaces, shell metacharacters, argv, environment and a
missing cwd. Inventory orders path strings, since `fs.Path` has no implicit
ordering. The updated local core retains `-O2`; its compile/link took 95,863 ms.
The signed development app is `../sputnik-notebook/build/Sputnik Notebook.app`
(minimum macOS 26.0 due to bundled dependencies).

Cell bytecode verification also recognizes a cell's private `rescue`/`ensure`
handlers, including nested handlers. Their slot reads and writes are checked
against the owning cell's manifest. Orphan, cyclic, cross-cell and ordinary-code
ownership are rejected. Handler code cannot be exposed as an ordinary closure
or module entry. Tests cover metadata rejection and actual transactional
publication of rescued and ensured cell outputs.

The GPU host behind external SSH port 22176 is **192.168.1.70**
(`ai-stt.orlando-labs.com`). Administrative access also works through `.155`:
SSH `-J lab.orlando-labs.com:22155`, target `192.168.1.70`, with
`HostKeyAlias=[lab.orlando-labs.com]:22176` preserving the known host key.

The public-hub 0.1.2 installation, canonical registry lock, CUDA VM and
full-native executable passed. The actual notebook API completed a fresh public
installation (job `f6bbb0ff6731e5ef5799304daefd8831`). A new project reused that
verified store and completed CUDA Run `30fb4b2d348678aaeaa2eea5cb16921d` on the
isolated core. The lock and native hashes remained enforced on Run.

The existing core on **18765** was then upgraded while idle. Its token was
preserved, with an online SQLite backup and previous process arguments in
`/home/slowpilot/sputnik-remote-core/backups/20261022-native`. The new core and
worker are in
`/home/slowpilot/sputnik-remote-core/releases/20261022-native`;
the agent uses the compiler at
`/ssd1Tb/slowpilot-sputnik-cuda-20261022/src/sputnik-lang/build/sputnik`.
Retain that SDK checkout while this release is active. The restart script is
`/home/slowpilot/sputnik-remote-core/start-native-20261022.sh`.

Production project **`cuda_registry_5d333cae1359`**, Run
**`b4bec6ef302c61fb5163a21455b5ba06`**, succeeded on the same exact package and
archive digest. It returned:

```text
PASS CUDA LibTorch 2.14.0 forward=14 gradient=[2,4,6] bytes=OK generator=OK device=cuda:0
```

This verifies actual GPU placement, forward/backward, CPU-to-GPU transfer,
the scoped invalid-device exception, bytes and seeded generation through the
notebook worker. The temporary core on 18766 was stopped after verification;
its projects and reports remain available on disk.

- [Fresh notebook API installation](evidence/2026-10-22/notebook-cuda-installation.json)
- [Isolated core CUDA Run](evidence/2026-10-22/notebook-cuda-isolated.json)
- [Production core CUDA Run](evidence/2026-10-22/notebook-cuda-production.json)
- [Core upgrade and health](evidence/2026-10-22/notebook-core-upgrade.json)
- [Handler verifier and VM regressions](evidence/2026-10-22/notebook-handler-tests.txt)

The same release also contains locally built macOS CPU/MPS prebuilt-runtime and
thin source variants; see [macOS publication](DEPLOYMENT_MACOS_2026_10_22.md).

## Quarantined earlier attempts

Versions 0.1.0 and 0.1.1 are quarantined and excluded from installation. 0.1.0
exposed the previous CPU/MPS-only device guard. 0.1.1 passed real CUDA in VM but
renamed the adapter without preserving its ELF SONAME, breaking standalone native
applications. Their bytes remain immutable for audit. The 0.1.1 local inspection
report is retained as
[historical inventory](evidence/2026-10-22/sputniktorch-cuda-package-0.1.1-quarantined.json).
