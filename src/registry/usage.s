package registry.usage

import sqlite3
import registry.storage as storage
from registry.domain import canonical, timestamp, ident, fail, reference, version, variant

export Usage, completed, initialize

def initialize(db):
  db.exec("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, version TEXT, variant TEXT, size INTEGER NOT NULL, complete INTEGER NOT NULL, created_at INTEGER NOT NULL); CREATE INDEX IF NOT EXISTS event_dates ON events(ref,created_at); CREATE TABLE IF NOT EXISTS watermark(id INTEGER PRIMARY KEY, at INTEGER NOT NULL); INSERT OR IGNORE INTO watermark VALUES(1,0)")

def transport_event(line):
  try:
    event = Json.parse(line, duplicate_keys: :reject)
    return null unless Map === event or StrictMap === event
    id = event[?"id"]
    return null unless Str === id and id.length == 32 and id.contains_only?("0123456789abcdef")
    return null unless event[?"kind"] == "download"
    reference(event[?"package_ref"])
    version(event[?"version"])
    variant(event[?"variant"])
    return null unless Int === event[?"size"] and event[:size] >= 0
    return null unless Int === event[?"created_at"] and event[:created_at] >= 0
    return null unless event[?"complete"] == true or event[?"complete"] == false
    event
  rescue Exception:
    null

def completed(root, artifact, success, bytes):
  event = {id: ident(), kind: "download", package_ref: artifact[:ref], version: artifact[:version], variant: artifact[:variant], size: bytes, complete: success and bytes == artifact[:size], created_at: storage.now()}
  storage.spool_append(root, canonical(event))

class Usage:
  def init(@store):
    @db = sqlite3.open(@store.root + "/usage.sqlite", busy_timeout: 5.0, pragmas: {journal_mode: "WAL", synchronous: "FULL"})
    initialize(@db)

  def close(): @db.close!()

  def project():
    # Exclude the current second: a concurrent commit may share its timestamp
    # while arriving after the pending-outbox query or spool rotation.
    projected_at = storage.now() - 1
    pending_at = @store.db |conn|:
      rows = conn.query("SELECT a.* FROM outbox o JOIN audit a ON a.id=o.id WHERE o.delivered=0 ORDER BY a.created_at LIMIT 100")
      @db.transaction(mode: :immediate) |db|:
        rows.each |row|:
          db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,1,?)", params: [row[:id], row[:kind], row[:ref], row[:version], row[:variant], row[:size], row[:created_at]])
      rows.each |row|:
        conn.execute("UPDATE outbox SET delivered=1 WHERE id=?", params: [row[:id]])
      conn.scalar("SELECT min(a.created_at) FROM outbox o JOIN audit a ON a.id=o.id WHERE o.delivered=0")
    projected_at = pending_at - 1 if pending_at != null and pending_at <= projected_at
    projected_at = 0 if projected_at < 0
    data = storage.spool_read(@store.root)
    @db.transaction(mode: :immediate) |db|:
      data.split("\n").each |line|:
        if line.empty?:
          next
        event = transport_event(line)
        unless event:
          storage.spool_mark_gap(@store.root)
          next
        db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)", params: [event[:id], event[:kind], event[:package_ref], event[:version], event[:variant], event[:size], (if event[:complete] then 1 else 0), event[:created_at]])
      db.execute("UPDATE watermark SET at=? WHERE id=1", params: [projected_at])
    storage.spool_ack(@store.root)

  def stats(ref, kind, start_at, to, ver, var):
    rows = @db.query("SELECT date(created_at,'unixepoch') AS day,count(*) AS count,sum(size) AS bytes,sum(CASE WHEN kind='release_published' THEN 1 ELSE 0 END) AS releases,sum(CASE WHEN kind='artifact_published' THEN 1 ELSE 0 END) AS artifacts FROM events WHERE ref=? AND created_at>=? AND created_at<? AND (? IS NULL OR version=?) AND (? IS NULL OR variant=?) AND ((?='downloads' AND kind='download' AND complete=1) OR (?='publications' AND kind IN ('release_published','artifact_published'))) GROUP BY day ORDER BY day LIMIT 366", params: [ref, start_at, to, ver, ver, var, var, kind, kind])
    {package_ref: ref, rows: rows, "from": timestamp(start_at), to: timestamp(to), watermark: timestamp(@db.scalar("SELECT at FROM watermark WHERE id=1")), coverage: {origin_only: true, best_effort: true, known_gap: storage.spool_gap(@store.root), crash_tail_unknown: true}}
