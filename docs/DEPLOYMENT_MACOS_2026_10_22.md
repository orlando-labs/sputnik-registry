# SputnikTorch macOS variants, 2026-10-22

Two variants of `slowpilot/sputniktorch@0.1.2` were built and published from the
local Mac to `https://registry.sputnik-lang.online`. The `sputniktorch` root alias
exposes the same release, alongside its existing Linux CUDA variant. Their
202 scoped source modules and release declarations match the CUDA release.

Both macOS variants target **aarch64, macOS >= 26.0**, the minimum of this
verified dependency build. They support actual CPU and MPS execution. The source
variant retains this verified target; it does not advertise other operating
systems or architectures.

| Variant | Native adapter | Bundled dependencies | Archive bytes |
|---|---|---|---:|
| `macos-aarch64-prebuilt-source-runtime` | prebuilt | LibTorch runtime closure | 94,707,304 |
| `macos-aarch64-source-source-none` | C++ source | none | 114,200 |

The prebuilt archive expands to 401,473,536 bytes; source to 672,256 bytes.
Exact archive SHA-256 values:

```text
macos-aarch64-prebuilt-source-runtime
sha256:9b66e12e33822000f97ef0e0bff2b5657c4e995e7bf084dc11a3edc2616dc8a1

macos-aarch64-source-source-none
sha256:9b004507a136165f3e910360cca1beb7d0b2c95a58ea21941b7db1d0d05e682c
```

The exact technical requirement is LibTorch **2.14.0**, ABI
`libtorch-2.14.0-macos-aarch64`, build identity
`sha256:ff327c4dd2f32d4858862f9b9f20c741ef4e300df1008d828268460b196aa7fe`.
The source variant also requires a matching `libtorch-sdk` provider, including
Gloo headers pinned to `74cc005ae13f69c11d8a41e50b42025b6730e796`.
It contains the adapter's own Sputnik/C++ sources and headers, with no third-party
runtime libraries or SDK assets. Consumer compilation needs a C++20 compiler
and the Apple platform SDK. Python is not used.

## Installation

Prebuilt CPU/MPS, without a consumer SDK or C++ compiler:

```sh
sputnik pkg install sputniktorch --version 0.1.2 \
  --backend mps --bundle runtime --allow-native --allow-native-large
```

Thin source, with explicit external providers from the matching dependency
build already present on the machine:

```sh
export SPUTNIK_NATIVE_PROVIDERS=/absolute/providers/runtime:/absolute/providers/sdk
sputnik pkg install sputniktorch --version 0.1.2 \
  --variant macos-aarch64-source-source-none --allow-native --build-from-source
```

Provider descriptors and their file inventories are checked before compilation.
Keep these bindings when using the source variant in VM or native builds.
To prepare the matching local descriptors from installed LibTorch and pinned
Gloo, use the [package recipe](../../sputniktorch/docs/package-building.md#scoped-macos-release-variants).
This local build's descriptors are in
`/Users/slowpilot/workspace/sputnik/sputniktorch/build/registry-012-macos-20261022/providers/{runtime,sdk}`.

The source package uses the standard size profile; the runtime package uses
native-large. Both retain the exact package's existing native publication grant.

## Verification and evidence

Both completed archives passed clean-store installation, exact variant/hash
locking, actual CPU/MPS VM execution and `--require-full-native` builds/runs.
MPS fallback was disabled throughout. Tests cover forward=14, gradient=[2,4,6],
CPU-to-MPS transfer, bytes, seeded generation and the scoped invalid-device
exception. Prebuilt installation/VM execution used an unavailable compiler and
no external providers. Source installation used the external runtime/SDK pair;
missing providers and LibTorch 2.11 were rejected before compilation.

The thin archive reproduced its exact hash with unavailable dependency and
compiler paths. Sputnik programs retain `-O2`; adapter code uses `-O3`.

Both variants were then installed again from the public hub through the root
alias into fresh stores. Their canonical locks bind the exact variant, archive
hash and registry origin. Actual CPU/MPS checks passed again, with fallback
disabled; the prebuilt package used no compiler or external provider bindings.

- [Public release metadata](evidence/2026-10-22/public-release.json)
- [MPS archive inventory](evidence/2026-10-22/sputniktorch-macos-mps-package.json)
- [Source archive inventory](evidence/2026-10-22/sputniktorch-macos-source-package.json)
- [Local verification](evidence/2026-10-22/macos-local-verification.json)
- [Public installation verification](evidence/2026-10-22/macos-public-verification.json)
- [MPS public lock](evidence/2026-10-22/sputnik-macos-mps.lock)
- [Source public lock](evidence/2026-10-22/sputnik-macos-source.lock)
- [MPS public VM result](evidence/2026-10-22/macos-public-mps-vm.txt)
- [Source public VM result](evidence/2026-10-22/macos-public-source-vm.txt)
- [MPS native result](evidence/2026-10-22/macos-mps-native.txt)
- [Source native result](evidence/2026-10-22/macos-source-native.txt)
