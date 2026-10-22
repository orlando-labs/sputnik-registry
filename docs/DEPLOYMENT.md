# Deployment: registry.sputnik-lang.online

Topology:

| Role | LAN address | SSH |
|---|---|---|
| nginx / TLS | 192.168.1.155 | `ssh -p 22155 lab.orlando-labs.com` |
| web / worker / storage | 192.168.1.97 | `ssh -p 22097 lab.orlando-labs.com` |
| Docker builder | 192.168.1.136 | `ssh -p 22136 lab.orlando-labs.com` |

The public origin is `https://registry.sputnik-lang.online`. The main hub uses
public reads and open GitHub Device Flow registration. Publication requires a
registry token obtained through `sputnik pkg login` or the operator console.
SDK HTTP installation/publication and operator-managed root aliases are
available. Detached signatures remain separate work.

## Build

From the local registry checkout, with sibling `sputnik-lang`, `orbit` and
`sqlite3` checkouts:

```sh
./tools/container-context
```

This archives the actual working sources, including non-ignored uncommitted files,
without local build outputs or `.git`. `SOURCE-MANIFEST.json` records each file's
SHA-256. Copy `.build/container-context.tar.gz` to a new builder directory and
extract it. Build on `.136`, not on the storage server:

```sh
docker build --progress=plain --build-arg BUILD_JOBS=16 \
  -f sputnik-registry/deploy/Dockerfile \
  -t docker.orlando-labs.com/sputnik-registry:UNIQUE_TAG .
docker push docker.orlando-labs.com/sputnik-registry:UNIQUE_TAG
```

The image requires a full-native build, native integration tests and both storage
test modes. A failure prevents creation of the runtime image. It contains the
test report, native build report, dependency fingerprints and source manifest in
`/opt/registry/evidence`. The runtime runs as UID/GID `10097:10097`.

The Debian 12 builder installs matching Clang/LLVM/LLD 19 packages. Generated
code and runtime use `-O2`; native extensions use `-O3`. The SDK splits large
LLVM modules into 16 partitions, optimizes them with 8 workers, and links with
ThinLTO to retain cross-partition inlining. `SPUTNIK_NATIVE_JOBS=1` selects the
serial `-O2` path for comparisons. Small programs use that path automatically.
There is no optimization-level wrapper. The image also runs the SDK's serial /
parallel semantics and large-graph tests. Compilation evidence is saved as
`/opt/registry/evidence/registry.native.cpp.compile.json`.

## Storage and configuration

On `.97`, `/opt/sputnik-registry/compose.yaml` comes from `deploy/compose.yaml`.
`.env` sets `REGISTRY_IMAGE` to the pushed image digest. Persistent paths:

| Host | Container | Contents |
|---|---|---|
| `/md0/sputnik-registry/config` | `/etc/sputnik-registry` (read-only) | private config and cursor secret |
| `/md0/sputnik-registry/data` | `/var/lib/sputnik-registry` | SQLite WAL, CAS, staging, spool |
| `/md0/sputnik-registry/backups` | mounted by operator when needed | verified snapshots |

All three directories are owned by `10097:10097`, mode `0700`; config is `0600`.
The bind mounts use shared SELinux labels for web and worker. Containers have a
read-only root, bounded logs, restart policy, dropped capabilities and a memory
limit (web 2 GiB, worker 512 MiB with no swap). `MALLOC_ARENA_MAX=2` bounds glibc
arena reservations on the many-core hosts. Worker address space is capped at
2 GiB to accommodate runtime thread stacks; this is separate from its 512 MiB
container memory limit. Only web publishes `192.168.1.97:18097`, forwarded by nginx on `.155`.
Inside the container the listener is `0.0.0.0:8080`; canonical origin and host
allowlist retain the public domain. Standard upload limit is 64 MiB.
The edge permits requests up to 2 GiB with streaming timeouts of 7200 seconds;
the application retains the 64 MiB standard profile. Native-large publication
additionally requires `native_large: true`, an operator `native-large` grant for
the exact package and its separate `native` grant. Large payloads remain streamed
through the 512 MiB worker rather than buffered as a whole file.

## Operations

On `.97`:

```sh
cd /opt/sputnik-registry
docker compose ps
docker compose logs --tail=100 web worker
docker compose pull
docker compose up -d
docker compose up -d --wait web
```

Operator commands use the same image/config and data mount:

```sh
docker compose run --rm --no-deps web account-create --label Publisher --namespace NAME
```

Capture the returned token into a private file; the server retains only its hash.
Initial tokens expire after 30 days. Never put tokens or `cursor_secret` in Git.

The main hub uses `read_access: "public"`; publication still requires a token.
SDK installs default to `https://registry.sputnik-lang.online`.

GitHub configuration uses `github_client_id` (public OAuth App Client ID) and
`registration: "open"`. Enable Device Flow in the GitHub OAuth App settings;
no client secret is needed. `registration: "closed"` restricts GitHub login to
already linked identities. Console provisioning and token issuance remain
available in either mode. The first GitHub login chooses a personal namespace;
the SDK then saves a private registry token for the exact origin. See
[RUNNING](RUNNING.md#github-login-and-self-service-publication) for CLI commands
and operator linking of an existing manually provisioned account.

After publishing `slowpilot/sputniktorch`, assign its permanent short name:

```sh
docker compose run --rm --no-deps web promote --ref slowpilot/sputniktorch
```

Users can then run `sputnik pkg install sputniktorch`. The package keeps its
scoped identity, owners and module names. Root aliases serve GET/HEAD only;
publish/yank/ownership operations use the scoped reference. Promotion is
idempotent and refuses names already used by another package, session or exact
unscoped grant. It is available only through the operator console, never HTTP.
Migration `002.sql` adds the alias table without changing migration `001.sql`.

Create a verified backup with a unique, absent output directory:

```sh
docker compose run --rm --no-deps \
  -v /md0/sputnik-registry/backups:/backup:rw,z \
  web backup --out /backup/UNIQUE_BACKUP_NAME
```

This uses the application's SQLite backup API and verifies CAS digests. Do not
copy live SQLite main files separately from WAL. Backups on `/md0` do not protect
against loss of that array; copy verified snapshots to independent storage.
An automatic backup schedule has not been configured.

For an update, take a backup first, retain the previous `.env`, set the new image
digest, then run the pull/up/readiness commands above. Worker has no HTTP
healthcheck; check its running state, restart count and logs separately. Restore the old
digest to roll back a binary-only update. Schema changes require a compatible
backup/restore plan; do not downgrade a migrated database blindly.

## nginx and certificates

`deploy/nginx.conf` is installed as
`/etc/nginx/conf.d/registry.sputnik-lang.online.conf` on `.155`.
TLS uses Certbot webroot `/var/www/letsencrypt`; the existing
`snap.certbot.renew.timer` renews certificates and the deploy hook tests/reloads
nginx. HTTP redirects to HTTPS except ACME challenges. Upload/download proxy
buffering, proxy cache and automatic upstream retries are disabled.

The nginx configuration before deployment is backed up under
`/root/registry-deploy-20261021/` on `.155`.

```sh
curl --fail https://registry.sputnik-lang.online/health/ready
curl --fail https://registry.sputnik-lang.online/api/v1/capabilities
```

Check usage DB/spool size and worker logs during operation. Retention, CAS orphan
GC and a prolonged load/soak gate remain application work described in RUNNING.md.

## Initial deployment result — 2026-10-21

- Image tag: `docker.orlando-labs.com/sputnik-registry:20261021-5`.
- Pinned image digest: `sha256:301d200121f69c0e9c30680c8c86c20995c08e6fdfc966056e2352f154f8dfc3`.
- Executable SHA-256: `a6bf87c8e26bd6477e907c4d8d48e1f9668fdcfcc6d973c9069a798a4c3baf1c`.
- Full native: 1928/1928 code bodies, zero VM fallback; 677 Linux integration
  checks and storage self-tests on both VM and native passed.
- nginx updated from 1.27.5 to 1.31.7 using its configured repository.
  Let's Encrypt certificate expires 2027-01-19; renewal dry-run passed.
- HTTPS readiness, authentication, token issue/revoke and anonymous-read denial
  verified. Initial namespace: `slowpilot`.
- Publisher credentials are on `.97` at
  `/opt/sputnik-registry/publisher-credentials.json`, owner `slowpilot`, mode
  `0600`. The initial token expires **2026-11-20 16:50:18 Europe/Moscow**.
  Account/token metadata without the secret is in `bootstrap-metadata.json`.
- Verified initial backup: `/md0/sputnik-registry/backups/20261021-initial`.
- A temporary instance on the actual 72-core `.97` host passed package publish,
  worker validation, download/digest and the 512 MiB worker memory limit with
  zero OOM events. Report: `/opt/sputnik-registry/node-verification.json`.
  The temporary containers and data were removed after success.

Build evidence files inside the image are private to root; an operator can read
them using `docker compose exec --user 0 web cat /opt/registry/evidence/FILE.json`.


## Optimized build — 2026-10-21

- Image: `docker.orlando-labs.com/sputnik-registry:20261021-6`.
- Pinned digest: `sha256:c40c1c49d3f3d5de9046c4038cc791f0c096f7e4da0e3fb62a5f33872ea571b1`.
- Executable SHA-256: `c14a053b69ff47c9d42a8496644bae0d2b505e26c46d72ada6a79f038a870767`.
- Generated code/runtime use `-O2`; extensions use `-O3`. Full native coverage
  remains 1928/1928, with no VM fallback.
- Native compile/link: 167.751 seconds, 8 workers / 16 LLVM partitions and
  ThinLTO. First registry build including a cold runtime archive: 298.8 seconds.
- Executable: 27,990,232 bytes; runtime image: 157,363,561 bytes uncompressed.
- All 677 integration checks, VM/native storage self-tests and the SDK's
  serial/parallel/large-graph gate passed in the Debian build image.
- Actual 72-core `.97` host passed package publish, worker validation and download
  digest checks under the 512 MiB worker limit with zero OOM events. Report:
  `/opt/sputnik-registry/node-verification-optimized.json`.
- Web and worker were switched to this digest; public HTTPS readiness, authenticated
  reads and token issue/revoke smoke checks passed after the update.
- Backup before switching images: `/md0/sputnik-registry/backups/20261021-before-optimized-6`.
  Previous image pin is retained in `/opt/sputnik-registry/.env.before-optimized-6`.

The application sources and migrations are unchanged from the initial deployment.
Compiler investigation and workload timings are in the SDK's
`docs/engineering/native-compile-time-2026-10-21.md`.

## Root aliases and default public hub — 2026-10-21

- Deployed image: `docker.orlando-labs.com/sputnik-registry:20261021-8`.
- Pinned digest: `sha256:47efa489aee33aea82f2e45c73908e0f63bc14fa301746aad1684c8f6125e3c6`.
- Executable SHA-256: `2588592eca25e8f39d9214b583ca9dc259f63e15232b01d9e71da16ab96e2bef`.
- Full native: 1931/1931 bodies, no VM fallback. Compile/link at `-O2` took
  169.074 seconds with 8 workers, 16 partitions and ThinLTO; the complete
  registry build with a warm runtime cache took 173.3 seconds.
- All 715 Linux integration checks, VM/native storage self-tests, the SDK
  serial/parallel native gate and HTTP installer contract passed in the image.
  Alias checks cover console promotion, read-only alias routes, collisions,
  idempotency, backup/restore, CLI fetch and SDK install/import.
- An isolated instance on `.97` passed publication, worker validation, operator
  promotion, anonymous alias metadata/download and anonymous write rejection.
  Worker memory events reported zero OOMs under its 512 MiB limit. Report:
  `/opt/sputnik-registry/node-verification-root-aliases.json`.
- Main config now has `read_access: "public"`; registration remains closed and
  all writes require credentials. HTTPS readiness, anonymous catalog reads,
  protected account/write endpoints and token issue/revoke passed after rollout.
- Migrations 1 and 2 are applied. Both production containers use the pinned
  digest, with zero restarts and no OOM at the final verification.
- Backup: `/md0/sputnik-registry/backups/20261021-before-root-aliases-8`;
  previous config: `20261021-config-before-root-aliases-8.json` in that parent;
  previous image pin: `/opt/sputnik-registry/.env.before-root-aliases-8`.
- The rebuilt local SDK defaults to this HTTPS hub. A request without
  `--registry` or credentials reached the public hub and returned the expected
  404 for a deliberately nonexistent package. Local filesystem, source directory,
  store, removal and distribution checks also passed.
- No packages were published to the production hub by this change. The example
  `slowpilot/sputniktorch` must be published before its root alias can be assigned.

The additional full VM regression run was stopped after the focused VM console
promotion/HTTP alias smoke passed; the 715-check result above is the complete
native integration run. Automatic platform/native variant selection remains
outside the portable source/bytecode HTTP installer.

## GitHub self-service login and SDK publication — 2026-10-21

- Image: `docker.orlando-labs.com/sputnik-registry:20261021-12`.
- Pinned digest: `sha256:4462d52a92dbd7a9f48755f5831ce9d5d736d3d2904f9cd70b4ec2a7257c6f1e`.
- Executable SHA-256: `432859d1dc0565cc73762fdee74f73e17adf913f86ffac600cb905abedd0bfca`.
- Full native: 1966/1966 functions, no VM fallback. Generated code/runtime use
  `-O2`, extensions retain `-O3`. Compile/link took 180.095 seconds; complete
  registry build with the runtime cache took 184.4 seconds.
- All 764 native integration checks, both storage modes, the SDK native parallel
  gate and HTTP installer checks passed in the image. A separate VM auth-only
  run passed 50 checks, including SDK login, token renewal after expiry,
  publish/resume, install, logout/revocation and console token compatibility.
- Isolated `.97` publication/worker/root-alias/download checks passed with zero
  OOMs under the 512 MiB worker memory limit.
- Main configuration now has `registration: "open"`, the configured GitHub
  OAuth App Client ID, and public reads. Migration 3 adds external identities and
  device-login attempt metadata; existing console accounts/tokens remain intact.
- Live HTTPS smoke passed readiness, public/protected routes, existing token
  issue/revoke, GitHub device-code creation, pending poll, login-secret rejection
  on the account API and attempt cancellation. New containers have zero
  restarts and no OOM at the final verification.
- Backup: `/md0/sputnik-registry/backups/20261021-before-github-12`;
  config backup: `20261021-config-before-github-12.json` in that parent;
  previous image: `/opt/sputnik-registry/.env.before-github-12`.
- Final image evidence is retained locally under `.build/github-image-evidence/`
  and in the runtime image under `/opt/registry/evidence`.

The final packaging update is `docker.orlando-labs.com/sputnik-registry:20261021-13`,
pinned to `sha256:376f0e8c232371eb72e25f92126564e908ca9ec6df863f1178b628bda9f2f4cf`.
It contains the same verified executable and test reports. Container-context
archives now preserve source modification times and timestamp their generated
manifest; this fixes a stale embedded source manifest observed with the earlier
zero-timestamp context. The complete 2202-file context and the source manifest
inside the final image were compared byte-for-byte. Live GitHub/console-token
smoke passed again after the packaging update; both containers report zero
restarts and no OOM. The final bundle is `.build/github-final-image-evidence/`.

The first optional real-user login attempt was cancelled without creating an
account or issuing a token. On 2026-10-22, a fresh Device Flow authorization
verified GitHub `orlando-labs` (numeric user ID `73169645`). At the user's
explicit request, the console `github-link` command linked that identity to the
existing `slowpilot` account `41e6eb30d0c8cf443cd83fc050a46e6a`.
The login completed against that same account and namespace. Its token has read,
token-management and create/publish permissions for `slowpilot`, and was saved
to the local SDK's default private credentials file. The original console token
remains valid. Subsequent `sputnik pkg login` calls retain the existing account
and namespace through the numeric GitHub identity.
