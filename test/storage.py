#!/usr/bin/env python3
"""Development-only runner for Sputnik CAS/spool fault tests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--native", action="store_true")
a = p.parse_args()
sdk = Path(os.environ.get("SPUTNIK", str(root.parent / "sputnik-lang/build/sputnik"))).resolve()
work = Path(tempfile.mkdtemp(prefix="storage-", dir=root / ".build"))
manifest = json.loads((root / ".build/registry.build.json").read_text())
manifest["root"] = "registry.storage_selftest"
manifest["modules"] = [m for m in manifest["modules"] if m["name"] in ["registry.storage", "sqlite3", "sdk.fs", "sdk.digest", "sdk.process", "sdk.pkg"]]
manifest["modules"].append({"name": "registry.storage_selftest", "path": str(root / "test/storage.s")})
if not a.native and sys.platform.startswith("linux"):
    # The VM executable exports the SDK symbols. Linking its non-PIC static
    # archive into the extension .so is both unnecessary and invalid on ELF.
    for extension in manifest["native_extensions"]:
        extension["link_libraries"] = [name for name in extension.get("link_libraries", []) if name != "sputnik"]
build = work / "storage.build.json"
build.write_text(json.dumps(manifest))
command = [str(sdk)]
grants = ["--grant", "ffi", "--grant", "fs.read", "--grant", "fs.write", "--grant", "fs.metadata"]
env = dict(os.environ)
env.setdefault("SPUTNIK_NATIVE_RT_CACHE", str(root / ".build/native-runtime"))
env.setdefault("SPUTNIK_NATIVE_RT_JOBS", "2")
if a.native:
    result = subprocess.run(command + ["build", str(build), "--target", "native", "--require-full-native", "--out-dir", str(work / "native")] + grants, env=env, capture_output=True, text=True)
    (work / "build.json").write_text(result.stdout)
    if result.returncode: raise RuntimeError(result.stderr + result.stdout)
    command = [json.loads(result.stdout)["native_output"]]
else:
    command += ["run", str(build)] + grants + ["--"]
subprocess.run(command + [str(work / "data")], cwd=root, env=env, check=True)
print(json.dumps({"passed": True, "backend": "native" if a.native else "vm", "work": str(work)}))
