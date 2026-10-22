package registry.store

import sqlite3
import registry.storage as storage
from registry.domain import fail, ident, sha, canonical, timestamp, profile, reference, scoped, require_scope, validate_scopes

export Store

class Store:
  attr config
  attr root

  def init(@config):
    @root = config[:data_dir]
    storage.storage_init(@root)
    @pool = sqlite3.pool(@root + "/metadata.sqlite", max_size: 8, busy_timeout: 5.0, pragmas: {journal_mode: "WAL", synchronous: "FULL", foreign_keys: true})
    migrate()

  def close(): @pool.close!()
  def db(&block): @pool.with_connection(&block)
  def transaction(&block):
    db() |conn|:
      conn.transaction(mode: :immediate, &block)

  def migrate():
    transaction() |conn|:
      conn.exec("CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)")
      ["001", "002", "003"].each |number|:
        sql = fs.read_text(@config[:migrations_dir] + "/" + number + ".sql")
        checksum = sha(sql)
        previous = conn.scalar("SELECT checksum FROM migrations WHERE version=?", params: [number.to_int])
        if previous:
          fail("MIGRATION_CHANGED", "Applied migration checksum differs", 500) unless previous == checksum
        else:
          conn.exec(sql)
          conn.execute("INSERT INTO migrations VALUES(?,?)", params: [number.to_int, checksum])

  def promote(conn, ref):
    reference(ref)
    fail("INVALID_REFERENCE", "Promotion requires a scoped package") unless scoped(ref)
    name = ref.split("/")[1]
    fail("NOT_FOUND", "Published package required", 404) unless conn.scalar("SELECT 1 FROM releases WHERE ref=?", params: [ref])
    previous = conn.scalar("SELECT ref FROM root_aliases WHERE name=?", params: [name])
    fail("ROOT_NAME_RESERVED", "Root name belongs to another package", 409) if previous and previous != ref
    unless previous:
      reserved = conn.scalar("SELECT 1 FROM packages WHERE ref=? UNION ALL SELECT 1 FROM sessions WHERE ref=? UNION ALL SELECT 1 FROM grants WHERE kind='unscoped' AND target=? LIMIT 1", params: [name, name, name])
      fail("ROOT_NAME_RESERVED", "Root name has already been used", 409) if reserved
      conn.execute("INSERT INTO root_aliases VALUES(?,?,?)", params: [name, ref, storage.now()])
      conn.execute("UPDATE catalog SET revision=revision+1 WHERE id=1")
      audit(conn, "root_alias_created", null, ref)
    {root_name: name, package_ref: ref}

  def account(conn, id):
    row = conn.query_one("SELECT * FROM accounts WHERE id=? AND active=1", params: [id])
    fail("UNAUTHORIZED", "Inactive account", 401) unless row
    row

  def actor_by_id(conn, id):
    row = conn.query_one("SELECT * FROM tokens WHERE id=? AND revoked=0 AND expires_at>?", params: [id, storage.now()])
    fail("UNAUTHORIZED", "Invalid or expired token", 401) unless row
    account(conn, row[:account_id])
    row[:scopes] = Json.parse(row[:scopes])
    row

  def authenticate(header):
    fail("UNAUTHORIZED", "Bearer token required", 401) unless Str === header and header.starts_with?("Bearer ") and header.length <= 256
    token = header.slice(7...header.length)
    db() |conn|:
      id = conn.scalar("SELECT id FROM tokens WHERE digest=?", params: [sha(token)])
      fail("UNAUTHORIZED", "Invalid or expired token", 401) unless id
      actor_by_id(conn, id)

  def grant?(conn, actor, kind, target):
    conn.scalar("SELECT 1 FROM grants WHERE account_id=? AND kind=? AND target IN (?, '*')", params: [actor[:account_id], kind, target]) != null

  def owner(conn, actor, ref, strict: false):
    role = conn.scalar("SELECT role FROM owners WHERE ref=? AND account_id=?", params: [ref, actor[:account_id]])
    fail("FORBIDDEN", "Package ownership required", 403) unless role and (not strict or role == "owner")
    role

  def authorize_publish(conn, actor, ref):
    fail("ROOT_ALIAS_READ_ONLY", "Publish using the canonical scoped reference", 409) if conn.scalar("SELECT 1 FROM root_aliases WHERE name=?", params: [ref])
    existing = conn.scalar("SELECT quota_owner FROM packages WHERE ref=?", params: [ref])
    if scoped(ref):
      slug = ref.split("/")[0]
      ns = conn.query_one("SELECT * FROM namespaces WHERE slug=? AND active=1", params: [slug])
      fail("FORBIDDEN", "Inactive or missing namespace", 403) unless ns
      if existing:
        owner(conn, actor, ref)
        require_scope(actor, "package:publish:" + ref)
      else:
        fail("FORBIDDEN", "Namespace ownership required", 403) unless ns[:account_id] == actor[:account_id]
        require_scope(actor, "namespace:create-package:" + slug)
      existing or "namespace:" + slug
    else:
      fail("FORBIDDEN", "Unscoped name requires operator grant", 403) unless grant?(conn, actor, "unscoped", ref)
      if existing: owner(conn, actor, ref)
      require_scope(actor, "package:publish:" + ref)
      unless existing:
        reserved_by = conn.scalar("SELECT account_id FROM sessions WHERE ref=? AND reserved=1 LIMIT 1", params: [ref])
        fail("FORBIDDEN", "Package is reserved by another publisher", 403) if reserved_by and reserved_by != actor[:account_id]
      existing or "account:" + actor[:account_id]

  def admit_profile(conn, actor, name, ref = "*"):
    profile(name)
    if name == "native-large":
      fail("FORBIDDEN", "native-large disabled or not granted", 403) unless @config[:native_large] and grant?(conn, actor, "native-large", ref)

  def audit(conn, kind, actor, ref = null, version = null, variant = null, size = 0):
    id = ident()
    conn.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)", params: [id, kind, actor, ref, version, variant, size, storage.now()])
    if kind == "artifact_published" or kind == "release_published":
      conn.execute("INSERT INTO outbox(id) VALUES(?)", params: [id])
    id

  def issue(conn, account_id, label, scopes, expiry):
    secret = "spreg_" + SecureRandom.hex(32)
    id = ident()
    conn.execute("INSERT INTO tokens(id,account_id,digest,prefix,label,scopes,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)", params: [id, account_id, sha(secret), secret.slice(0...14), label, canonical(scopes), expiry, storage.now()])
    {id: id, token: secret, expires_at: timestamp(expiry), scopes: scopes}

  def bootstrap(label, namespace):
    reference(namespace + "/validation")
    transaction() |conn|:
      fail("NAMESPACE_EXISTS", "Namespace already exists", 409) if conn.scalar("SELECT 1 FROM namespaces WHERE slug=?", params: [namespace])
      id = ident()
      conn.execute("INSERT INTO accounts(id,label,created_at) VALUES(?,?,?)", params: [id, label, storage.now()])
      conn.execute("INSERT INTO namespaces(slug,account_id) VALUES(?,?)", params: [namespace, id])
      scopes = ["registry:read", "namespace:create-package:" + namespace, "namespace:publish:" + namespace, "token:manage"]
      result = issue(conn, id, "bootstrap", scopes, storage.now() + 2592000)
      result[:account_id] = id
      result[:namespace] = namespace
      audit(conn, "account_created", id)
      result

  def write(actor, action, key, data, &block):
    valid = Str === key and key.length >= 16 and key.length <= 128
    if valid:
      valid = key.contains_only?(" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~")
    fail("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 16 to 128 printable ASCII characters") unless valid
    request_hash = sha(canonical(data))
    transaction() |conn|:
      current = actor_by_id(conn, actor[:id])
      previous = conn.query_one("SELECT * FROM idempotency WHERE account_id=? AND action=? AND key=? AND expires_at>?", params: [current[:account_id], action, key, storage.now()])
      if previous:
        fail("IDEMPOTENCY_CONFLICT", "Key was used with different input", 409) unless previous[:request_hash] == request_hash
        # Domain authorization runs before this method as well as within writes.
        result = [previous[:status], Json.parse(previous[:response])]
        next result
      result = block(conn, current)
      conn.execute("INSERT OR REPLACE INTO idempotency VALUES(?,?,?,?,?,?,?)", params: [current[:account_id], action, key, request_hash, canonical(result[1]), result[0], storage.now() + 604800])
      result

  def rate(conn, bucket, maximum):
    window = storage.now() / 60
    conn.execute("INSERT INTO rate_limits VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN window=excluded.window THEN count+1 ELSE 1 END,window=excluded.window", params: [bucket, window])
    fail("RATE_LIMITED", "Rate limit exceeded", 429) if conn.scalar("SELECT count FROM rate_limits WHERE bucket=?", params: [bucket]) > maximum

  def expire(conn):
    now = storage.now()
    conn.execute("UPDATE sessions SET state='expired',reserved=0,reason_code='SESSION_EXPIRED' WHERE reserved=1 AND state IN ('created','uploaded','receiving','cancelled') AND expires_at<? AND receiving_until<?", params: [now, now])
    conn.execute("UPDATE sessions SET state=CASE WHEN upload_key IS NULL THEN 'created' ELSE 'uploaded' END,receiving_until=0,worker_fence=NULL WHERE state='receiving' AND receiving_until<? AND expires_at>?", params: [now, now])
    conn.execute("UPDATE sessions SET reserved=0 WHERE state='cancelled' AND receiving_until<?", params: [now])
    conn.execute("DELETE FROM idempotency WHERE expires_at<?", params: [now])

  def usage(conn, actor):
    pending = conn.scalar("SELECT coalesce(sum(size),0) FROM sessions WHERE account_id=? AND reserved=1", params: [actor[:account_id]])
    traffic = conn.scalar("SELECT coalesce(sum(bytes),0) FROM traffic WHERE account_id=? AND created_at>?", params: [actor[:account_id], storage.now() - 86400])
    {pending_bytes: pending, upload_bytes_24h: traffic, authoritative: true}

  def cleanup():
    transaction() |conn|:
      expire(conn)
      storage.staging_keys(@root).each |entry|:
        if entry[1] < storage.now() - 3600:
          live = conn.scalar("SELECT 1 FROM sessions WHERE reserved=1 AND (upload_key=? OR worker_fence=?)", params: [entry[0], entry[0]])
          storage.remove(@root, entry[0]) unless live
      conn.execute("DELETE FROM traffic WHERE created_at<?", params: [storage.now() - 172800])
      conn.execute("DELETE FROM rate_limits WHERE window<?", params: [storage.now() / 60 - 10])
