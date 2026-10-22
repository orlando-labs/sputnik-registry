package registry

import orbit
import orbit.telemetry as orbit_telemetry
import registry.storage as storage
import registry.http as http
import registry.cli as cli
import registry.client as client
import registry.backup as backup
from registry.store import Store
from registry.publish import Publisher
from registry.usage import Usage
from registry.domain import fail, canonical, ident, reference, timestamp, validate_scopes

export main

def report_error(event):
  io.stderr.write_line("orbit: " + event.error.message)

def configuration(path):
  fail("CONFIG_REQUIRED", "Set SPUTNIK_REGISTRY_CONFIG") if path.empty?
  config = Json.parse(fs.read_text(path), duplicate_keys: :reject)
  allowed = ["data_dir", "origin", "host", "port", "allowed_hosts", "read_access", "native_large", "cursor_secret", "migrations_dir", "github_client_id", "registration"]
  config.each |key, value|:
    fail("INVALID_CONFIG", "Unknown configuration field") unless allowed.contains?(key.to_str)
  fail("INVALID_CONFIG", "Absolute data_dir required") unless Str === config[?"data_dir"] and config[:data_dir].starts_with?("/")
  origin = config[?"origin"]
  fail("INVALID_CONFIG", "Canonical origin required") unless Str === origin and not origin.ends_with?("/") and (origin.starts_with?("https://") or origin.starts_with?("http://127.0.0.1:") or origin.starts_with?("http://localhost:"))
  config[:host] = "127.0.0.1" unless config.has_key?("host")
  config[:port] = 8080 unless config.has_key?("port")
  config[:allowed_hosts] = ["127.0.0.1", "localhost"] unless config.has_key?("allowed_hosts")
  config[:read_access] = "authenticated" unless config.has_key?("read_access")
  config[:native_large] = false unless config.has_key?("native_large")
  config[:migrations_dir] = "migrations" unless config.has_key?("migrations_dir")
  config[:registration] = "closed" unless config.has_key?("registration")
  config[:github_client_id] = "" unless config.has_key?("github_client_id")
  fail("INVALID_CONFIG", "Invalid registration mode") unless ["open", "closed"].contains?(config[:registration])
  fail("INVALID_CONFIG", "Invalid GitHub client ID") unless Str === config[:github_client_id] and config[:github_client_id].length <= 128 and (config[:github_client_id].empty? or config[:github_client_id].contains_only?("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"))
  fail("INVALID_CONFIG", "Open registration requires GitHub client ID") if config[:registration] == "open" and config[:github_client_id].empty?
  fail("INVALID_CONFIG", "Invalid read access") unless ["public", "authenticated"].contains?(config[:read_access])
  fail("INVALID_CONFIG", "cursor_secret needs 32 random bytes encoded as hex") unless Str === config[?"cursor_secret"] and config[:cursor_secret].length == 64 and config[:cursor_secret].contains_only?("0123456789abcdef")
  config

def operator(store, data):
  command = data[:command]
  if command == "account-create": return store.bootstrap(data[:label], data[:namespace])
  store.transaction |conn|:
    if command == "github-link":
      store.account(conn, data[:account_id])
      subject = data[:subject]
      fail("INVALID_SUBJECT", "Expected GitHub numeric user ID") unless Str === subject and subject.length > 0 and subject.length <= 24 and subject.contains_only?("0123456789") and not subject.starts_with?("0")
      existing = conn.scalar("SELECT account_id FROM external_identities WHERE provider='github' AND subject=?", params: [subject])
      fail("IDENTITY_LINKED", "GitHub identity already belongs to another account", 409) if existing and existing != data[:account_id]
      conn.execute("INSERT OR IGNORE INTO external_identities VALUES('github',?,?,?,?)", params: [subject, data[:account_id], "", storage.now()])
      store.audit(conn, "operator_identity_linked", data[:account_id])
      linked = {linked: true, provider: "github", subject: subject, account_id: data[:account_id]}
      next linked
    if command == "package-promote": next store.promote(conn, data[:package_ref])
    if command == "grant":
      store.account(conn, data[:account_id])
      fail("INVALID_GRANT", "Unknown grant kind") unless ["native", "native-large", "unscoped"].contains?(data[:kind])
      target = data[:target]
      reference(target) unless target == "*"
      conn.execute("INSERT OR IGNORE INTO grants VALUES(?,?,?)", params: [data[:account_id], data[:kind], target])
      store.audit(conn, "operator_grant", data[:account_id], target)
      block_result = {granted: true}
      next block_result
    if command == "token-issue":
      store.account(conn, data[:account_id])
      scopes = validate_scopes(data[:scopes])
      next store.issue(conn, data[:account_id], data[:label], scopes, storage.now() + 2592000)
    if command == "account-suspend":
      conn.execute("UPDATE accounts SET active=0 WHERE id=?", params: [data[:account_id]])
      store.audit(conn, "account_suspended", data[:account_id])
      block_result = {suspended: true}
      next block_result
    if command == "quarantine":
      ref = reference(data[:package_ref])
      if data[?"variant"]:
        conn.execute("UPDATE artifacts SET quarantined=? WHERE ref=? AND version=? AND variant=?", params: [(if data[:enabled] then 1 else 0), ref, data[:version], data[:variant]])
      else:
        conn.execute("UPDATE releases SET quarantined=? WHERE ref=? AND version=?", params: [(if data[:enabled] then 1 else 0), ref, data[:version]])
      conn.execute("UPDATE packages SET revision=revision+1 WHERE ref=?", params: [ref])
      conn.execute("UPDATE catalog SET revision=revision+1 WHERE id=1")
      store.audit(conn, "operator_quarantine", null, ref, data[:version], data[?"variant"])
      block_result = {quarantined: data[:enabled]}
      next block_result
    fail("UNKNOWN_COMMAND", "Unknown operator command")

def main():
  storage.harden()
  args = cli.arguments()
  command = args[:command]
  if command == "init":
    io.stdout.write_line(canonical(cli.init_config(args)))
    return null
  if command == "publish" or command == "fetch":
    result = if command == "publish" then client.publish(args) else client.fetch(args)
    io.stdout.write_line(canonical(result))
    return null
  if command == "worker":
    io.stdout.write_line(canonical(cli.supervise(args)))
    return null
  config = configuration(args[:config])
  if command == "backup" or command == "restore":
    result = if command == "backup" then backup.create(config, args[:out]) else backup.restore(config, args[:backup])
    io.stdout.write_line(canonical(result))
    return null
  store = Store(config)
  try:
    if command == "operator":
      data = cli.operator_input(args)
      io.stdout.write_line(canonical(operator(store, data)))
    elif command == "promote":
      result = operator(store, {command: "package-promote", package_ref: cli.required(args, "ref")})
      io.stdout.write_line(canonical(result))
    elif command == "account-create":
      io.stdout.write_line(canonical(store.bootstrap(cli.required(args, "label"), cli.required(args, "namespace"))))
    elif command == "worker-once":
      storage.worker_limits()
      lock = storage.File(store.root, "worker", "lock", 0)
      usage = Usage(store)
      try:
        processed = Publisher(store).work_one()
        store.cleanup()
        usage.project()
        io.stdout.write_line(canonical({processed: processed}))
      ensure:
        usage.close()
        lock.destroy!()
    elif command == "migrate":
      io.stdout.write_line(canonical({migrated: true}))
    elif command == "serve":
      service = http.Service(store)
      app = orbit.App(routes: http.routes(), services: {registry: service}, security: orbit.SecurityPolicy(allowed_hosts: config[:allowed_hosts]), on_error: &report_error)
      app.run(host: config[:host], port: config[:port], workers: 4, max_concurrent_per_worker: 16, max_header_bytes: 16384, max_body_bytes: (if config[:native_large] then 2147483648 else 67108864), read_timeout: 30.0, write_timeout: 30.0)
    else:
      fail("UNKNOWN_COMMAND", "Expected init, serve, worker, worker-once, migrate, account-create, operator, publish, fetch, backup or restore")
  ensure:
    store.close()
