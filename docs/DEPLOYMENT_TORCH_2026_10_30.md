# Torch 0.1.3 publication and registry validator

The 30 October 2026 deployment updated the registry to understand the shared
normalized package manifests emitted by current Sputnik. The exact source
commits are Sputnik `1b5bbfa`, registry `23b9a77`, Orbit `ecb0d11` and sqlite3
`5ef7fdd`. The complete full-native build passed 772 integration checks,
VM/native storage checks, package CLI checks and native-parallel checks.
The registry contains 1,985 native bodies with zero VM fallback bodies.

Production uses immutable Docker image ID
`sha256:2c7b382ba85cf6594b2463c39643e1916b10305ad680540c24808668c0f5ca28`.
The image was transferred directly from the approved builder because that host
had no Docker registry publishing credentials. No credentials were copied.

The verified native backup is
`/md0/sputnik-registry/backups/before-rocm-validator-20261030T063250Z`.
Account, namespace, package, release, artifact and token counts matched before
and after switching both production containers. The native backup verified all
51 existing artifacts. Existing publisher credentials and public-read policy
were preserved. The unused temporary read token was revoked.

The published `slowpilot/torch@0.1.3` set contains all three previous variants
plus ROCm:

| Variant | SHA-256 |
| --- | --- |
| `macos-aarch64-prebuilt-source-runtime` | `0cc6072e5ae1a370d9e99c26c850feb1ac5306743d5bf69e1f3156e80635cf36` |
| `macos-aarch64-source-source-none` | `65b5fcbe3598ef4ae12b7164bbfb6ef42c269174a6b66c11a5f0b54bfabe774d` |
| `linux-x86_64-gnu-cu130-prebuilt-source-runtime` | `e57ee53c2a26a09584c547e668e159a2de4c0c19f41d5a459c1ebb2235b8d335` |
| `linux-x86_64-gnu-rocm643-prebuilt-source-runtime` | `c99e2897c0e99363492f9430ddb235b031b0f8b8ca4948933927ae14728f6baf` |

Public metadata hashes match the locally verified archives. Both macOS variants
were installed into empty package homes from the public registry and executed on
CPU/MPS with CPU fallback disabled. Both Linux archives were installed from the
public registry and tested on real GPUs in VM and full-native execution.

## Temporary availability failures

All times below are UTC (add three hours for Moscow).

- At 06:30:23 and 06:30:34 nginx returned `502` for the package index with
  `connect() failed (113: No route to host)` to the upstream. This overlapped the
  first container switch. Its deployment script incorrectly checked the host
  HTTP address, which returned 403, and rolled back. The corrected script checks
  the container's own readiness endpoint; the final deployment completed at
  06:35:15. A short maintenance interruption during that update is confirmed.
- At approximately 06:38–06:41 external TLS operations and SSH banner exchanges
  to multiple lab servers timed out. The new web container continued running,
  had no OOM/restart, and subsequent internal health checks were successful.
  At 06:41:11 a macOS upload ended with HTTP 500 and the registry logged a TCP
  read timeout. Retrying the same saved publication session succeeded.
- Index requests returned 200 at 06:39:11 and 06:39:22; the macOS upload completed
  at 06:46:19 and all four variants were visible at 06:47:09. An old version list
  followed by the new release is expected around atomic publication. A TLS
  timeout is a separate transport failure, not the package index hiding a
  committed release.

The later network failure's exact cause is unproven. The overlapping SSH
failures support a path/connectivity problem beyond the registry application;
they do not establish a specific router, saturation or packet-loss cause.
The nginx error log does not identify TLS handshake failures for that period.

Sputnik `5d291d7` subsequently changed client install/import defaults to
host-compatible automatic backend and bundle selection. This is client resolver
behavior and does not require changing the immutable published Torch payloads or
the registry archive inspector. Explicit policies and exact optional locks
retain their effect.
