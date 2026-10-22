package registry.backup

import sqlite3
import registry.storage as storage
import registry.usage as usage
from registry.domain import fail, canonical, ident
from registry.cli import write_new

export create, restore

def check_db(path):
  sqlite3.open(path, busy_timeout: 5.0) |db|:
    fail("CORRUPT_BACKUP", "Database integrity check failed") unless db.scalar("PRAGMA integrity_check") == "ok"
    fail("CORRUPT_BACKUP", "Database foreign key check failed") unless db.query("PRAGMA foreign_key_check").empty?

def digest_path(digest):
  fail("INVALID_DIGEST", "Invalid blob digest") unless Str === digest and digest.length == 64 and digest.contains_only?("0123456789abcdef")
  "blobs/" + digest.slice(0...2) + "/" + digest

def create(config, output):
  fail("ARGUMENT_REQUIRED", "--out is required") unless Str === output and output.length > 0
  fs.mkdir(output)
  source = config[:data_dir]
  files = {}
  ["metadata.sqlite", "usage.sqlite"].each |name|:
    if fs.exists?(source + "/" + name):
      storage.sqlite_snapshot(source + "/" + name, output + "/" + name)
      check_db(output + "/" + name)
  artifacts = []
  sqlite3.open(output + "/metadata.sqlite") |db|:
    artifacts = db.query("SELECT DISTINCT digest,size FROM artifacts")
    db.execute("UPDATE sessions SET state='failed',reserved=0,reason_code='RESTORED_WITHOUT_STAGING' WHERE reserved=1")
    db.execute("UPDATE outbox SET delivered=0")
  if fs.exists?(output + "/usage.sqlite"):
    live_events = {}
    live_artifacts = {}
    sqlite3.open(output + "/metadata.sqlite") |metadata|:
      metadata.each("SELECT id FROM audit WHERE kind IN ('release_published','artifact_published')") |row|:
        live_events[row[:id]] = true
      metadata.each("SELECT ref,version,variant FROM artifacts") |row|:
        live_artifacts[canonical([row[:ref], row[:version], row[:variant]])] = true
    sqlite3.open(output + "/usage.sqlite") |db|:
      usage.initialize(db)
      db.transaction(mode: :immediate) |conn|:
        last = ""
        loop:
          rows = conn.query("SELECT id,kind,ref,version,variant FROM events WHERE id>? ORDER BY id LIMIT 100", params: [last])
          if rows.empty?:
            break
          rows.each |row|:
            keep = if row[:kind] == "download" then live_artifacts.has_key?(canonical([row[:ref], row[:version], row[:variant]])) else live_events.has_key?(row[:id])
            conn.execute("DELETE FROM events WHERE id=?", params: [row[:id]]) unless keep
            last = row[:id]
  ["metadata.sqlite", "usage.sqlite"].each |name|:
    path = output + "/" + name
    if fs.exists?(path):
      fs.sync(path)
      files[name] = storage.hash_file(path)
  artifacts.each |row|:
    relative = digest_path(row[:digest])
    path = source + "/" + relative
    fail("CAS_CORRUPTION", "Referenced blob has wrong size or digest") unless fs.metadata(path).size == row[:size] and storage.hash_file(path) == row[:digest]
    destination = output + "/" + relative
    fs.mkdir_p(fs.Path(destination).parent)
    storage.copy_new(path, destination)
    files[relative] = row[:digest]
  manifest = {schema: "sputnik.registry.backup.v1", origin: config[:origin], files: files, artifacts: artifacts.count, pending_sessions: "failed-on-restore", transport_coverage: "best-effort-snapshot"}
  write_new(output + "/backup.json", manifest)
  fs.sync(output)
  {backup: storage.absolute(output), artifacts: artifacts.count}

def verify_name(name, digest):
  return true if ["metadata.sqlite", "usage.sqlite"].contains?(name)
  expected = digest_path(digest)
  fail("INVALID_BACKUP_PATH", "Invalid backup member path") unless name == expected
  true

def verify_artifacts(directory, files):
  check_db(directory + "/metadata.sqlite")
  sqlite3.open(directory + "/metadata.sqlite", mode: :readonly) |db|:
    rows = db.query("SELECT DISTINCT digest,size FROM artifacts")
    rows.each |row|:
      name = digest_path(row[:digest])
      fail("INVALID_BACKUP", "Referenced blob is missing from inventory") unless files[?name] == row[:digest]
      path = directory + "/" + name
      fail("BACKUP_CORRUPTION", "Referenced blob is missing or has wrong size") unless fs.file?(path) and fs.metadata(path).size == row[:size]
      fail("BACKUP_CORRUPTION", "Referenced blob digest differs") unless storage.hash_file(path) == row[:digest]
    rows.count

def restore(config, source):
  fail("ARGUMENT_REQUIRED", "--backup is required") unless Str === source and source.length > 0
  data = config[:data_dir]
  fail("RESTORE_CONFLICT", "Restore requires an absent data_dir") if fs.exists?(data)
  manifest = Json.parse(fs.read_text(source + "/backup.json", limit: 16777216), duplicate_keys: :reject)
  fail("BACKUP_MISMATCH", "Backup schema or origin differs") unless manifest[:schema] == "sputnik.registry.backup.v1" and manifest[:origin] == config[:origin]
  fail("INVALID_BACKUP", "Metadata snapshot is missing") unless manifest[:files].has_key?("metadata.sqlite")
  manifest[:files].each |name, digest|:
    verify_name(name, digest)
    path = source + "/" + name
    fail("BACKUP_CORRUPTION", "Backup member digest differs") unless storage.hash_file(path) == digest
  count = verify_artifacts(source, manifest[:files])
  # Only the verified complete directory is installed at the requested path.
  staging = data + ".restore-" + ident()
  fs.mkdir(staging)
  manifest[:files].each |name, digest|:
    destination = staging + "/" + name
    fs.mkdir_p(fs.Path(destination).parent)
    storage.copy_new(source + "/" + name, destination)
  ["metadata.sqlite", "usage.sqlite"].each |name|:
    check_db(staging + "/" + name) if fs.exists?(staging + "/" + name)
  verify_artifacts(staging, manifest[:files])
  fs.mkdir(staging + "/staging")
  fs.mkdir(staging + "/spool")
  write_new(staging + "/spool/gap", {restored: true})
  fs.sync(staging)
  fail("RESTORE_CONFLICT", "Restore destination appeared during restore") if fs.exists?(data)
  fs.rename(staging, data)
  fs.sync(fs.Path(storage.absolute(data)).parent)
  {restored: data, artifacts: count}
