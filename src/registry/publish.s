package registry.publish

import registry.storage as storage
import registry.domain as domain
from registry.domain import fail, ident, sha, canonical, timestamp, reference, version, version_key, variant, profile, scoped

export Publisher

class Publisher:
  def init(@store)

  def session(conn, actor, id):
    row = conn.query_one("SELECT * FROM sessions WHERE id=? AND account_id=?", params: [id, actor[:account_id]])
    fail("NOT_FOUND", "Publication session not found", 404) unless row
    row

  def view(row):
    result = {id: row[:id], state: row[:state], variant: row[:variant], limit_profile: row[:profile], expires_at: timestamp(row[:expires_at]), status_url: @store.config[:origin] + "/api/v1/publish-sessions/" + row[:id]}
    if row[:state] == "created":
      result[:upload_method] = "PUT"
      result[:upload_url] = result[:status_url] + "/artifact"
      result[:upload_headers] = {"Content-Type": "application/vnd.sputnik.package.v1"}
    if ["uploaded", "validating", "promoting", "published"].contains?(row[:state]):
      result[:sha256] = "sha256:" + row[:digest]
      result[:size] = row[:size]
    if row[:state] == "published":
      result[:package_ref] = row[:ref]
      result[:version] = row[:version]
      result[:artifact_url] = artifact_url(row[:ref], row[:version], row[:variant])
    if row[:reason_code]:
      result[:reason_code] = row[:reason_code]
      result[:retryable] = row[:reason_code] == "RETRY_PENDING"
    result

  def artifact_url(ref, ver, var):
    base = if scoped(ref) then "/namespaces/" + ref.split("/")[0] + "/packages/" + ref.split("/")[1] else "/packages/" + ref
    @store.config[:origin] + "/api/v1" + base + "/versions/" + ver + "/artifacts/" + var

  def create(actor, key, data):
    ref = reference(data[:package_ref])
    ver = version(data[:version])
    var = variant(data[:variant])
    name = data[?"limit_profile"] or "standard"
    limit = profile(name)
    size = data[:expected_size]
    digest = data[:expected_sha256]
    fail("INVALID_SIZE", "Invalid expected artifact size") unless Int === size and size > 0 and size <= limit[:artifact_bytes]
    fail("INVALID_DIGEST", "Expected SHA-256 digest") unless Str === digest and digest.length == 71 and digest.starts_with?("sha256:") and digest.slice(7...71).contains_only?("0123456789abcdef")
    @store.db |conn|:
      @store.authorize_publish(conn, actor, ref)
      @store.admit_profile(conn, actor, name, ref)
    @store.write(actor, "create-session:" + ref, key, data) |conn, current|:
      @store.expire(conn)
      quota = @store.authorize_publish(conn, current, ref)
      @store.admit_profile(conn, current, name, ref)
      @store.rate(conn, "create:" + current[:account_id], 30)
      fail("ARTIFACT_EXISTS", "Artifact is immutable and already published", 409) if conn.scalar("SELECT 1 FROM artifacts WHERE ref=? AND version=? AND variant=?", params: [ref, ver, var])
      fail("ARTIFACT_RESERVED", "Variant is reserved by another session", 409) if conn.scalar("SELECT 1 FROM sessions WHERE ref=? AND version=? AND variant=? AND reserved=1", params: [ref, ver, var])
      release = conn.query_one("SELECT * FROM releases WHERE ref=? AND version=?", params: [ref, ver])
      if release:
        fail("RELEASE_UNAVAILABLE", "Cannot append to yanked or quarantined release", 409) if release[:yanked] == 1 or release[:quarantined] == 1
      count = conn.scalar("SELECT count(*) FROM artifacts WHERE ref=? AND version=?", params: [ref, ver]) + conn.scalar("SELECT count(*) FROM sessions WHERE ref=? AND version=? AND reserved=1", params: [ref, ver])
      fail("VARIANT_LIMIT", "Release variant limit reached", 429) if count >= 32
      active = conn.scalar("SELECT count(*) FROM sessions WHERE reserved=1")
      own = conn.scalar("SELECT count(*) FROM sessions WHERE reserved=1 AND account_id=?", params: [current[:account_id]])
      fail("SESSION_LIMIT", "Active session limit reached", 429) if active >= 64 or own >= 3
      global_bytes = conn.scalar("SELECT coalesce(sum(size),0) FROM sessions WHERE reserved=1 AND profile=?", params: [name])
      own_bytes = conn.scalar("SELECT coalesce(sum(size),0) FROM sessions WHERE reserved=1 AND account_id=? AND profile=?", params: [current[:account_id], name])
      fail("PENDING_QUOTA", "Pending upload quota exceeded", 429) if global_bytes + size > limit[:pending_global] or own_bytes + size > limit[:pending_account]
      stored = conn.scalar("SELECT coalesce(sum(a.size),0) FROM artifacts a JOIN packages p ON p.ref=a.ref WHERE p.quota_owner=? AND a.profile=?", params: [quota, name])
      reserved = conn.scalar("SELECT coalesce(sum(size),0) FROM sessions WHERE quota_owner=? AND profile=? AND reserved=1", params: [quota, name])
      fail("STORAGE_QUOTA", "Logical storage quota exceeded", 429) if stored + reserved + size > limit[:storage_bytes]
      now = storage.now()
      id = ident()
      conn.execute("INSERT INTO sessions(id,account_id,token_id,ref,version,variant,digest,size,profile,quota_owner,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,'created',?,?)", params: [id, current[:account_id], current[:id], ref, ver, var, digest.slice(7...71), size, name, quota, now, now + limit[:session_seconds]])
      @store.audit(conn, "session_created", current[:account_id], ref, ver, var)
      [201, view(session(conn, current, id))]

  def get(actor, id):
    @store.transaction |conn|:
      @store.expire(conn)
      view(session(conn, actor, id))

  def authorize_session(actor, id):
    @store.db |conn|:
      row = session(conn, actor, id)
      @store.authorize_publish(conn, actor, row[:ref])
      row

  def authorize_initiator(conn, row):
    initiating = @store.actor_by_id(conn, row[:token_id])
    @store.authorize_publish(conn, initiating, row[:ref])
    @store.admit_profile(conn, initiating, row[:profile], row[:ref])

  def upload(actor, id, request):
    fail("UNSUPPORTED_ENCODING", "Content-Encoding is not supported", 415) if request.header("content-encoding")
    fail("UNSUPPORTED_MEDIA_TYPE", "Expected package media type", 415) unless request.header("content-type") == "application/vnd.sputnik.package.v1"
    traffic_id = ident()
    upload_key = ident()
    replay = false
    row = @store.transaction |conn|:
      @store.expire(conn)
      current = @store.actor_by_id(conn, actor[:id])
      current_row = session(conn, current, id)
      authorize_initiator(conn, current_row)
      @store.authorize_publish(conn, current, current_row[:ref])
      @store.admit_profile(conn, current, current_row[:profile], current_row[:ref])
      fail("UPLOAD_IN_PROGRESS", "Upload already in progress", 409) if current_row[:state] == "receiving"
      fail("SESSION_STATE_CONFLICT", "Session does not accept uploads", 409) unless ["created", "uploaded"].contains?(current_row[:state])
      declared = request.header("content-length")
      if declared:
        fail("INVALID_SIZE", "Content-Length differs from session") unless declared.to_int == current_row[:size]
      replay = current_row[:state] == "uploaded"
      receiving = conn.scalar("SELECT count(*) FROM sessions WHERE receiving_until>?", params: [storage.now()])
      own = conn.scalar("SELECT count(*) FROM sessions WHERE account_id=? AND receiving_until>?", params: [current[:account_id], storage.now()])
      fail("UPLOAD_CAPACITY", "Upload capacity exhausted", 503) if receiving >= 2 or own >= 1
      if current_row[:profile] == "native-large":
        fail("UPLOAD_CAPACITY", "Large upload already running", 503) if conn.scalar("SELECT count(*) FROM sessions WHERE profile='native-large' AND receiving_until>?", params: [storage.now()]) > 0
      limit = profile(current_row[:profile])
      charged = conn.scalar("SELECT coalesce(sum(bytes),0) FROM traffic WHERE account_id=? AND profile=? AND created_at>?", params: [current[:account_id], current_row[:profile], storage.now() - 86400])
      fail("TRAFFIC_QUOTA", "Daily upload traffic quota exceeded", 429) if charged + current_row[:size] > limit[:traffic_bytes]
      conn.execute("INSERT INTO traffic VALUES(?,?,?,?,?)", params: [traffic_id, current[:account_id], current_row[:profile], current_row[:size], storage.now()])
      conn.execute("UPDATE sessions SET state='receiving',receiving_until=?,worker_fence=? WHERE id=?", params: [storage.now() + limit[:upload_seconds] + 35, upload_key, id])
      current_row
    total = 0
    file = null
    finished = false
    begin_time = storage.now()
    try:
      file = storage.File(@store.root, upload_key, "write", row[:size])
      while chunk = request.body_stream.read_chunk(max_bytes: 65536):
        bytes = chunk.bytes
        total = total + bytes.count
        fail("UPLOAD_TIMEOUT", "Upload total deadline exceeded", 408) if storage.now() - begin_time > profile(row[:profile])[:upload_seconds]
        fail("UPLOAD_CONFLICT", "Uploaded bytes exceed session size", 409) if total > row[:size]
        file.write(bytes)
      hash = file.finish()
      fail("UPLOAD_CONFLICT", "Uploaded size or SHA-256 differs", 409) unless total == row[:size] and hash == row[:digest]
      @store.transaction |conn|:
        current = @store.actor_by_id(conn, actor[:id])
        current_row = session(conn, current, id)
        authorize_initiator(conn, current_row)
        @store.authorize_publish(conn, current, row[:ref])
        fail("SESSION_STATE_CONFLICT", "Upload lease was fenced", 409) unless current_row[:state] == "receiving" and current_row[:worker_fence] == upload_key and current_row[:receiving_until] > storage.now()
        conn.execute("UPDATE sessions SET state='uploaded',upload_key=?,receiving_until=0,worker_fence=NULL WHERE id=?", params: [(if replay then row[:upload_key] else upload_key), id])
      finished = true
      {id: id, state: "uploaded", sha256: "sha256:" + hash, size: total}
    ensure:
      file.destroy!() if file
      storage.remove(@store.root, upload_key) if not finished or replay
      @store.transaction |conn|:
        conn.execute("UPDATE traffic SET bytes=? WHERE id=?", params: [total, traffic_id])
        conn.execute("UPDATE sessions SET state=?,receiving_until=0,worker_fence=NULL WHERE id=? AND state='receiving' AND worker_fence=?", params: [(if replay then "uploaded" else "created"), id, upload_key])
        conn.execute("UPDATE sessions SET reserved=0,receiving_until=0 WHERE id=? AND state='cancelled' AND worker_fence=?", params: [id, upload_key])

  def commit(actor, id, key, data):
    authorize_session(actor, id)
    @store.db |conn|:
      authorize_initiator(conn, session(conn, actor, id))
    @store.write(actor, "commit:" + id, key, data) |conn, current|:
      row = session(conn, current, id)
      authorize_initiator(conn, row)
      @store.authorize_publish(conn, current, row[:ref])
      if ["published", "validating", "promoting"].contains?(row[:state]):
        block_result = [(if row[:state] == "published" then 200 else 202), view(row)]
        next block_result
      fail("SESSION_STATE_CONFLICT", "Only uploaded sessions can be committed", 409) unless row[:state] == "uploaded" and row[:expires_at] > storage.now()
      conn.execute("UPDATE sessions SET state='validating',lease_until=0 WHERE id=?", params: [id])
      @store.audit(conn, "session_committed", current[:account_id], row[:ref], row[:version], row[:variant])
      [202, view(session(conn, current, id))]

  def cancel(actor, id, key, data):
    authorize_session(actor, id)
    @store.write(actor, "cancel:" + id, key, data) |conn, current|:
      row = session(conn, current, id)
      @store.authorize_publish(conn, current, row[:ref])
      fail("SESSION_STATE_CONFLICT", "Session cannot be cancelled", 409) unless ["created", "receiving", "uploaded", "cancelled"].contains?(row[:state])
      reserved = if row[:receiving_until] > storage.now() then row[:reserved] else 0
      conn.execute("UPDATE sessions SET state='cancelled',reserved=? WHERE id=?", params: [reserved, id])
      @store.audit(conn, "session_cancelled", current[:account_id], row[:ref], row[:version], row[:variant])
      [200, view(session(conn, current, id))]

  def work_one():
    fence = ident()
    row = @store.transaction |conn|:
      @store.expire(conn)
      job = conn.query_one("SELECT * FROM sessions WHERE state IN ('validating','promoting') AND lease_until<? ORDER BY created_at LIMIT 1", params: [storage.now()])
      if job:
        conn.execute("UPDATE sessions SET worker_fence=?,lease_until=?,attempts=attempts+1 WHERE id=?", params: [fence, storage.now() + 960, job[:id]])
      job
    return false unless row
    phase = "inspection"
    try:
      info = Json.parse(storage.inspect(@store.root, row[:upload_key], row[:profile]), duplicate_keys: :reject)
      dist = info[:distribution]
      manifest = info[:manifest]
      descriptor = info[:descriptor]
      fail("ARTIFACT_IDENTITY_MISMATCH", "Archive does not match publication session") unless dist[:name] == row[:ref] and dist[:version] == row[:version] and dist[:variant] == row[:variant] and dist[:sha256] == "sha256:" + row[:digest] and dist[:compressed_size] == row[:size] and descriptor[:limit_profile] == row[:profile]
      fail("METADATA_LIMIT", "Too many modules or dependencies") if manifest[:modules].count > 2048 or manifest[:dependencies].count > 256
      if scoped(row[:ref]):
        prefix = row[:ref].split("/")[0] + "."
        manifest[:modules].each |mod|:
          fail("MODULE_PREFIX", "Scoped modules must use publisher prefix") unless mod[:name].starts_with?(prefix)
      domain.validate_dependencies(manifest[:dependencies], descriptor[:dependencies], descriptor[:build_dependencies])
      modules = manifest[:modules].map |mod|:
        mod[:name]
      common = {requires: manifest[:requires], kind: manifest[:kind], root_module: manifest[:root_module], modules: modules, dependencies: manifest[:dependencies], capabilities: manifest[:capabilities]}
      common_json = canonical(common)
      fail("METADATA_LIMIT", "Release metadata too large") if common_json.bytes.count > 786432
      descriptor[:schema] = "sputnik.registry.artifact.v1"
      descriptor[:package_ref] = row[:ref]
      descriptor[:version] = row[:version]
      descriptor[:variant] = row[:variant]
      descriptor[:format] = "sputnik.pkg.v1"
      descriptor[:sha256] = "sha256:" + row[:digest]
      descriptor[:size] = row[:size]
      descriptor[:url] = artifact_url(row[:ref], row[:version], row[:variant]) + "/download"
      descriptor[:signatures] = []
      descriptor_json = canonical(descriptor)
      fail("METADATA_LIMIT", "Artifact metadata too large") if descriptor_json.bytes.count > 786432
      @store.transaction |conn|:
        current = check_job(conn, row, fence)
        @store.authorize_publish(conn, current, row[:ref])
        @store.admit_profile(conn, current, row[:profile], row[:ref])
        native = descriptor[:native_payload] or descriptor[:native_mode] != "none" or descriptor[:kind] != "library" or descriptor[:bundle] != "none"
        fail("NATIVE_GRANT_REQUIRED", "Native publication requires operator grant", 403) if native and not @store.grant?(conn, current, "native", row[:ref])
        existing = conn.query_one("SELECT * FROM releases WHERE ref=? AND version=?", params: [row[:ref], row[:version]])
        if existing:
          fail("RELEASE_UNAVAILABLE", "Release unavailable", 409) if existing[:yanked] == 1 or existing[:quarantined] == 1
          fail("RELEASE_METADATA_CONFLICT", "Release declarations differ between variants", 409) unless existing[:common_hash] == sha(common_json)
        conn.execute("UPDATE sessions SET state='promoting' WHERE id=?", params: [row[:id]])
      phase = "promotion"
      storage.promote(@store.root, row[:upload_key], row[:digest])
      @store.transaction |conn|:
        current = check_job(conn, row, fence)
        quota = @store.authorize_publish(conn, current, row[:ref])
        @store.admit_profile(conn, current, row[:profile], row[:ref])
        native = descriptor[:native_payload] or descriptor[:native_mode] != "none" or descriptor[:kind] != "library" or descriptor[:bundle] != "none"
        fail("NATIVE_GRANT_REQUIRED", "Native publication requires operator grant", 403) if native and not @store.grant?(conn, current, "native", row[:ref])
        existing = conn.query_one("SELECT * FROM releases WHERE ref=? AND version=?", params: [row[:ref], row[:version]])
        if existing:
          fail("RELEASE_UNAVAILABLE", "Release unavailable", 409) if existing[:yanked] == 1 or existing[:quarantined] == 1
          fail("RELEASE_METADATA_CONFLICT", "Release declarations differ between variants", 409) unless existing[:common_hash] == sha(common_json)
        else:
          if not conn.scalar("SELECT 1 FROM packages WHERE ref=?", params: [row[:ref]]):
            conn.execute("INSERT INTO packages(ref,quota_owner,created_at) VALUES(?,?,?)", params: [row[:ref], quota, storage.now()])
            conn.execute("INSERT INTO owners VALUES(?,?,'owner')", params: [row[:ref], current[:account_id]])
          conn.execute("INSERT INTO releases(ref,version,sort_key,common_json,common_hash,published_at) VALUES(?,?,?,?,?,?)", params: [row[:ref], row[:version], version_key(row[:version]), common_json, sha(common_json), storage.now()])
          @store.audit(conn, "release_published", current[:account_id], row[:ref], row[:version])
        # Reserve headroom for the release envelope and index pagination fields.
        release_bytes = common_json.bytes.count + canonical(domain.compact_artifact(descriptor)).bytes.count + 4096
        conn.query("SELECT metadata FROM artifacts WHERE ref=? AND version=?", params: [row[:ref], row[:version]]).each |artifact|:
          release_bytes += canonical(domain.compact_artifact(Json.parse(artifact[:metadata]))).bytes.count + 1
        fail("METADATA_LIMIT", "Release with all variants exceeds response limit") if release_bytes > 1000000
        conn.execute("INSERT INTO artifacts(ref,version,variant,digest,size,metadata,profile,published_at) VALUES(?,?,?,?,?,?,?,?)", params: [row[:ref], row[:version], row[:variant], row[:digest], row[:size], descriptor_json, row[:profile], storage.now()])
        conn.execute("UPDATE packages SET revision=revision+1 WHERE ref=?", params: [row[:ref]])
        conn.execute("UPDATE catalog SET revision=revision+1 WHERE id=1")
        conn.execute("UPDATE sessions SET state='published',reserved=0,completed_at=?,lease_until=0,reason_code=NULL WHERE id=? AND worker_fence=?", params: [storage.now(), row[:id], fence])
        @store.audit(conn, "artifact_published", current[:account_id], row[:ref], row[:version], row[:variant], row[:size])
      storage.remove(@store.root, row[:upload_key])
    rescue Exception |error|:
      code = "INVALID_ARTIFACT"
      if domain.Error === error: code = error.code
      terminal = @store.transaction |conn|:
        owned = conn.scalar("SELECT 1 FROM sessions WHERE id=? AND worker_fence=? AND lease_until>? AND state IN ('validating','promoting')", params: [row[:id], fence, storage.now()])
        if not owned:
          next false
        if phase == "promotion" and not (domain.Error === error) and row[:attempts] < 4:
          conn.execute("UPDATE sessions SET lease_until=?,reason_code='RETRY_PENDING' WHERE id=? AND worker_fence=?", params: [storage.now() + 5, row[:id], fence])
          next false
        conn.execute("UPDATE sessions SET state='rejected',reserved=0,reason_code=?,completed_at=?,lease_until=0 WHERE id=? AND worker_fence=?", params: [code, storage.now(), row[:id], fence])
        @store.audit(conn, "artifact_rejected", row[:account_id], row[:ref], row[:version], row[:variant])
        true
      storage.remove(@store.root, row[:upload_key]) if terminal
    true

  def check_job(conn, row, fence):
    current = conn.query_one("SELECT * FROM sessions WHERE id=? AND worker_fence=? AND lease_until>? AND state IN ('validating','promoting')", params: [row[:id], fence, storage.now()])
    fail("LEASE_LOST", "Worker lease expired", 409) unless current
    @store.actor_by_id(conn, row[:token_id])
