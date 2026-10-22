package registry.build_tool

export main

def absolute(path, root):
  fs.Path(if path.starts_with?("/") then path else root + "/" + path).normalize.to_str

def fingerprint(files):
  result = {}
  files.each |file|:
    result[file] = Digest.sha256(fs.read_bytes(file, limit: 268435456)).hex()
  result

def flags(package_name, option):
  system.command("pkg-config", option, package_name).output(timeout: 10.0).trim.split(" ").reject |word|:
    word.empty?

def main():
  parser = ArgParser(name: "build Sputnik registry")
  parser.arg("--root", required: true)
  parser.arg("--sdk")
  parser.arg("--orbit")
  parser.arg("--sqlite")
  parser.flag("--manifest-only")
  args = parser.parse_or_raise()
  root = args[:root]
  sdk = absolute(args[:sdk] or "../sputnik-lang", root)
  orbit = absolute(args[:orbit] or "../orbit", root)
  sqlite = absolute(args[:sqlite] or "../sqlite3", root)
  output = root + "/.build"
  fs.mkdir_p(output)
  modules = [{name: "registry", path: root + "/src/registry.s"}]
  ["auth", "domain", "http", "storage", "publish", "store", "usage", "cli", "client", "backup"].each |name|:
    modules.push!({name: "registry." + name, path: root + "/src/registry/" + name + ".s"})
  modules.push!({name: "orbit", path: orbit + "/src/orbit.s"})
  modules.push!({name: "orbit.telemetry", path: orbit + "/src/orbit/telemetry.s"})
  modules.push!({name: "sqlite3", path: sqlite + "/src/sqlite3.s"})
  ["fs", "digest", "process", "pkg"].each |name|:
    modules.push!({name: "sdk." + name, path: sdk + "/packages/system_io/src/" + name + ".s"})
  includes = [sdk, sqlite + "/native"]
  directories = [sdk + "/build"]
  ["openssl", "libarchive"].each |package_name|:
    folder = if package_name == "openssl" then "openssl@3" else package_name
    prefix = ["/opt/homebrew/opt/" + folder, "/usr/local/opt/" + folder].find |path|:
      fs.exists?(path) and fs.dir?(path)
    if prefix:
      includes.push!(prefix + "/include")
      directories.push!(prefix + "/lib")
    else:
      flags(package_name, "--cflags-only-I").each |flag|:
        includes.push!(flag.slice(2...flag.length))
      flags(package_name, "--libs-only-L").each |flag|:
        directories.push!(flag.slice(2...flag.length))
  native_sources = [sdk + "/packages/system_io/native/system_io.cpp"]
  extensions = [
    {name: "system_io", language: "c++", sources: native_sources, include_dirs: includes, library_dirs: directories, cxxflags: ["-std=c++17"], link_libraries: ["sputnik", "archive", "z", "ssl", "crypto"]},
    {name: "sqlite3", language: "c", sources: [sqlite + "/native/sqlite3_ext.c", sqlite + "/native/sqlite3.c"], headers: [sqlite + "/native/sqlite3.h"], defines: ["SQLITE_THREADSAFE=1", "SQLITE_ENABLE_COLUMN_METADATA=1", "SQLITE_DQS=0", "SQLITE_OMIT_LOAD_EXTENSION=1"]}
  ]
  manifest = {schema: "sputnik.build.v1", name: "registry", root: "registry", profiles: {required: ["core.v1", "macro.v1", "ffi.v1"], optional: [], forbidden: []}, modules: modules, native_extensions: extensions}
  path = output + "/registry.build.json"
  fs.atomic_write_text(path, Json.pretty_generate(manifest) + "\n")
  files = [sdk + "/build/sputnik", sdk + "/build/libsputnik.a", root + "/migrations/001.sql", root + "/migrations/002.sql", root + "/migrations/003.sql", path, sqlite + "/native/sqlite3_ext.c", sqlite + "/native/sqlite3.c", sqlite + "/native/sqlite3.h"] + native_sources
  modules.each |mod|:
    files.push!(mod[:path])
  inputs = fingerprint(files)
  fs.atomic_write_text(output + "/inputs.json", Json.pretty_generate(inputs) + "\n")
  unless args[:manifest_only]:
    command = ["build", path, "--target", "native", "--require-full-native", "--out-dir", output + "/native"]
    ["ffi", "net.listen", "net.connect", "random.secure", "fs.read", "fs.write", "fs.metadata", "process.spawn", "process.signal"].each |grant|:
      command.push!("--grant")
      command.push!(grant)
    result = system.command(sdk + "/build/sputnik", *command).capture(timeout: 900.0, check: false)
    fs.atomic_write_bytes(output + "/compiler.stderr.log", result.stderr)
    fs.atomic_write_bytes(output + "/build-result.json", result.stdout)
    raise ValueError("Native build failed; see .build/build-result.json and compiler.stderr.log") unless result.success?
    raise ValueError("Inputs changed during compilation; rebuild") unless Json.generate(fingerprint(files)) == Json.generate(inputs)
    report = Json.parse(result.stdout.to_str)
    raise ValueError("Full native coverage required") unless report[:native_graph_vm_independent] and report[:native_graph_full_coverage]
    io.stdout.write_line(Json.generate({status: report[:status], native_output: report[:native_output], native_hash: report[:native_hash], code_count: report[:native_graph_code_count], native_code_count: report[:native_graph_native_code_count]}))
  io.stdout.write_line(path)
