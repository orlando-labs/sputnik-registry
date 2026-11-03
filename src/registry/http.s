package registry.http

import orbit
import registry.storage as storage
import registry.domain as domain
from registry.domain import fail, ident, sha, canonical, timestamp, reference, version, version_key, variant, require_scope, scope_allows, validate_scopes
from registry.publish import Publisher
from registry.usage import Usage, completed
from registry.auth import DeviceLogin

export Service, dispatch, routes

def dispatch(request): request.services[:registry].call(request)

_routes = orbit.routes:
  get "/health/live", to: &dispatch
  get "/health/ready", to: &dispatch
  get "/api/v1/*path", to: &dispatch
  head "/api/v1/*path", to: &dispatch
  post "/api/v1/*path", to: &dispatch
  put "/api/v1/*path", to: &dispatch
  delete "/api/v1/*path", to: &dispatch

def routes(): _routes

class Service:
  def init(@store):
    @publisher = Publisher(@store)
    @login = DeviceLogin(@store)

  def response(data, status = 200, headers = {}):
    headers["content-type"] = "application/vnd.sputnik.registry.v1+json"
    headers["cache-control"] = "no-store"
    orbit.Response(status: status, body: canonical(data), headers: headers)

  def body(request, fields, required = [], limit = 65536):
    fail("UNSUPPORTED_MEDIA_TYPE", "Expected JSON", 415) unless ["application/json", "application/vnd.sputnik.registry.v1+json"].contains?((request.header("content-type") or "").split(";")[0])
    fail("UNSUPPORTED_ENCODING", "Content-Encoding is not supported", 415) if request.header("content-encoding")
    text = ""
    count = 0
    while chunk = request.body_stream.read_chunk(max_bytes: 8192):
      count += chunk.bytes.count
      fail("BODY_TOO_LARGE", "JSON body limit exceeded", 413) if count > limit
      text += chunk.text
    parsed = Json.parse(text, duplicate_keys: :reject)
    fail("INVALID_REQUEST", "Expected JSON object") unless Map === parsed or StrictMap === parsed
    parsed.each |key, value|:
      fail("UNKNOWN_FIELD", "Unknown JSON field") unless fields.contains?(key.to_str)
    result = {}
    fields.each |key|:
      result.store!(key, parsed[key]) if parsed.has_key?(key)
    required.each |key|:
      fail("INVALID_REQUEST", "Missing field: " + key) unless result.has_key?(key)
    result

  def call(request):
    request_id = ident()
    try:
      result = route(request)
      result.with_header("x-request-id", request_id)
    rescue domain.Error |error|:
      headers = {"x-request-id": request_id}
      headers["www-authenticate"] = "Bearer" if error.status == 401
      headers["retry-after"] = "5" if error.status == 429 or error.status == 503
      detail = {code: error.code, message: error.message, request_id: request_id}
      detail[:suggestions] = error.suggestions unless error.suggestions == null
      response({error: detail}, error.status, headers)
    rescue JsonParseError:
      response({error: {code: "INVALID_JSON", message: "Invalid JSON document", request_id: request_id}}, 400, {"x-request-id": request_id})
    rescue Exception |error|:
      io.stderr.write_line("registry request " + request_id + ": " + error.message)
      response({error: {code: "INTERNAL_ERROR", message: "Internal server error", request_id: request_id}}, 500, {"x-request-id": request_id})

  def route(request):
    path = request.path
    method = request.method
    read = method == "GET" or method == "HEAD"
    if path == "/health/live" and read: return response({status: "alive"})
    if path == "/health/ready" and read:
      @store.db |conn|:
        conn.scalar("SELECT 1")
      return response({status: "ready"})
    if path == "/api/v1/capabilities" and read: return response(capabilities())
    if path == "/api/v1/auth/device" and method == "POST":
      data = body(request, ["provider", "expected_account_id"], ["provider"], 4096)
      return response(@login.start(data, request.client_ip or "unknown"), 201)
    if path.starts_with?("/api/v1/auth/device/"):
      parts = path.split("/")
      id = parts[5]
      if parts.count == 6 and method == "DELETE":
        return response(@login.cancel(id, request.header("authorization")))
      if parts.count == 7 and parts[6] == "poll" and method == "POST":
        body(request, [], [], 4096)
        return response(@login.poll(id, request.header("authorization")))
      if parts.count == 7 and parts[6] == "complete" and method == "POST":
        data = body(request, ["namespace", "token"], [], 8192)
        return response(@login.complete(id, request.header("authorization"), request.header("idempotency-key"), data), 201)
      fail("NOT_FOUND", "Unknown login endpoint", 404)
    actor = null
    if not read or @store.config[:read_access] == "authenticated" or request.header("authorization"):
      actor = @store.authenticate(request.header("authorization"))
    require_scope(actor, "registry:read") if read and actor
    bucket = if actor then "read:" + actor[:account_id] else "anonymous:" + (request.client_ip or "unknown")
    @store.transaction |conn|:
      @store.rate(conn, bucket, (if actor then 1000 else 300))
    if path == "/api/v1/me" and read:
      fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
      return @store.db |conn|:
        response({account: @store.account(conn, actor[:account_id]), token_id: actor[:id], scopes: actor[:scopes], grants: conn.query("SELECT kind,target FROM grants WHERE account_id=?", params: [actor[:account_id]])})
    if path == "/api/v1/me/usage" and read:
      fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
      return @store.db |conn|:
        response(@store.usage(conn, actor))
    if path == "/api/v1/tokens" or path.starts_with?("/api/v1/tokens/"):
      fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
      return tokens(request, actor)
    if path == "/api/v1/publish-sessions" and method == "POST":
      data = body(request, ["package_ref", "version", "variant", "expected_size", "expected_sha256", "limit_profile"], ["package_ref", "version", "variant", "expected_size", "expected_sha256"])
      result = @publisher.create(actor, request.header("idempotency-key"), data)
      return response(result[1], result[0])
    if path.starts_with?("/api/v1/publish-sessions/"):
      fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
      parts = path.split("/")
      id = parts[4]
      if parts.count == 5 and read: return response(@publisher.get(actor, id))
      if parts.count == 6 and parts[5] == "artifact" and method == "PUT":
        return response(@publisher.upload(actor, id, request))
      data = body(request, [])
      if parts.count == 6 and parts[5] == "commit" and method == "POST":
        result = @publisher.commit(actor, id, request.header("idempotency-key"), data)
        return response(result[1], result[0])
      if parts.count == 5 and method == "DELETE":
        result = @publisher.cancel(actor, id, request.header("idempotency-key"), data)
        return response(result[1], result[0])
    if path == "/api/v1/packages" and read: return search(request)
    parts = path.split("/")
    ref = null
    index = 0
    if parts.count >= 5 and parts[3] == "packages":
      ref = reference(parts[4])
      index = 5
    elif parts.count >= 7 and parts[3] == "namespaces" and parts[5] == "packages":
      ref = reference(parts[4] + "/" + parts[6])
      index = 7
    if ref:
      tail = if index == parts.count then [] else parts[index...parts.count]
      return package_route(request, actor, ref, tail)
    fail("NOT_FOUND", "Unknown endpoint", 404)

  def capabilities():
    profiles = {standard: domain.limits()["standard"]}
    profiles["native-large"] = domain.limits()["native-large"] if @store.config[:native_large]
    result = {schema: "sputnik.registry.capabilities.v1", protocol: 1, features: ["scoped-references", "root-aliases", "exact-dependencies", "language-semver", "module-source", "module-bytecode", "artifact-variants", "prebuilt-native"], artifact_formats: ["sputnik.pkg.v1"], trust_profile: "private", read_access: @store.config[:read_access], registration: @store.config[:registration], login_providers: (if @login.enabled? then ["github"] else []), native_targets: [], limit_profiles: profiles, limits: {page_entries: 100, release_variants: 32, receiving_uploads_global: 2, receiving_uploads_account: 1, active_sessions_global: 64, active_sessions_account: 3}}

    result[:features].push!("device-login") if @login.enabled?
    result

  def tokens(request, actor):
    self_revoke = request.method == "DELETE" and request.path == "/api/v1/tokens/" + actor[:id]
    require_scope(actor, "token:manage") unless self_revoke
    if request.path == "/api/v1/tokens" and request.method == "GET":
      return @store.db |conn|:
        response({tokens: conn.query("SELECT id,prefix,label,scopes,expires_at,revoked,created_at FROM tokens WHERE account_id=? ORDER BY created_at", params: [actor[:account_id]])})
    if request.path == "/api/v1/tokens" and request.method == "POST":
      data = body(request, ["label", "scopes", "expires_at"], ["label", "scopes"], 16384)
      validate_scopes(data[:scopes])
      fail("INVALID_LABEL", "Invalid token label") unless Str === data[:label] and data[:label].length > 0 and data[:label].bytes.count <= 128
      expiry = actor[:expires_at]
      if data.has_key?("expires_at"):
        expiry = domain.expiry_seconds(data[:expires_at])
      fail("INVALID_EXPIRY", "Token expiry exceeds issuing token") unless expiry > storage.now() and expiry <= actor[:expires_at] and expiry <= storage.now() + 7776000
      data[:scopes].each |scope|:
        fail("SCOPE_ESCALATION", "Token cannot delegate this scope", 403) unless scope_allows(actor[:scopes], scope)
      # Never persist a recoverable secret in an idempotency response.
      key = request.header("idempotency-key")
      result = null
      @store.write(actor, "issue-token", key, data) |conn, current|:
        result = @store.issue(conn, current[:account_id], data[:label], data[:scopes], expiry)
        @store.audit(conn, "token_created", current[:account_id])
        [409, {error: {code: "TOKEN_SECRET_ALREADY_ISSUED", message: "Token secret is shown once; revoke its id and create another"}, id: result[:id]}]
      return response(result, 201) if result
      fail("TOKEN_SECRET_ALREADY_ISSUED", "Token secret is shown once; list and revoke if response was lost", 409)
    if request.method == "DELETE" and request.path.split("/").count == 5:
      id = request.path.split("/")[4]
      data = body(request, [], [], 16384)
      result = @store.write(actor, "revoke-token:" + id, request.header("idempotency-key"), data) |conn, current|:
        fail("NOT_FOUND", "Token not found", 404) unless conn.scalar("SELECT 1 FROM tokens WHERE id=? AND account_id=?", params: [id, current[:account_id]])
        conn.execute("UPDATE tokens SET revoked=1 WHERE id=? AND account_id=?", params: [id, current[:account_id]])
        @store.audit(conn, "token_revoked", current[:account_id])
        [200, {id: id, revoked: true}]
      return response(result[1], result[0])
    fail("NOT_FOUND", "Unknown token endpoint", 404)

  def page_size(request, default):
    raw = request.query("limit")
    return default unless raw
    fail("INVALID_PAGE", "Invalid page size") unless Str === raw and raw.length > 0 and raw.contains_only?("0123456789")
    while raw.length > 1 and raw.starts_with?("0"):
      raw = raw.slice(1...raw.length)
    fail("INVALID_PAGE", "Page size must be between 1 and 100") if raw.length > 3
    value = raw.to_int
    fail("INVALID_PAGE", "Page size must be between 1 and 100") unless value >= 1 and value <= 100
    value

  def cursor(request, target, revision):
    raw = request.query("cursor")
    return "" unless raw
    fail("INVALID_CURSOR", "Invalid cursor") unless Str === raw and raw.bytes.count <= 2048
    parts = raw.split(".")
    fail("INVALID_CURSOR", "Invalid cursor") unless parts.count == 2
    decoded = domain.cursor_text(parts[0])
    expected = Digest.hmac_sha256(@store.config[:cursor_secret].bytes, decoded.bytes).hex()
    fail("INVALID_CURSOR", "Invalid cursor signature") unless expected == parts[1]
    data = Json.parse(decoded)
    fail("INVALID_CURSOR", "Cursor expired or belongs to another query") unless data[:target] == target and data[:expires_at] > storage.now()
    fail("INDEX_CHANGED", "Index changed; restart pagination", 409) unless data[:revision] == revision
    data[:last]

  def next_cursor(target, revision, last):
    data = canonical({target: target, revision: revision, last: last, expires_at: storage.now() + 900})
    Base64.encode(data.bytes) + "." + Digest.hmac_sha256(@store.config[:cursor_secret].bytes, data.bytes).hex()

  def search(request):
    query = request.query("query") or ""
    fail("INVALID_QUERY", "Search query too long") unless Str === query and query.bytes.count <= 128
    size = page_size(request, 20)
    @store.db |conn|:
      revision = conn.scalar("SELECT revision FROM catalog WHERE id=1")
      target = "search:" + query
      last = cursor(request, target, revision)
      rows = conn.query("SELECT ref,revision,created_at,(SELECT name FROM root_aliases WHERE root_aliases.ref=packages.ref) AS root_name FROM packages WHERE ref>? AND instr(ref,?)>0 AND EXISTS(SELECT 1 FROM releases WHERE releases.ref=packages.ref AND quarantined=0) ORDER BY ref LIMIT ?", params: [last, query, size + 1])
      more = rows.count > size
      rows = rows[0...size] if more
      response({packages: rows, revision: revision, next_cursor: if more then next_cursor(target, revision, rows[rows.count - 1][:ref]) else null})

  def release_data(conn, ref, ver):
    release = conn.query_one("SELECT * FROM releases WHERE ref=? AND version=?", params: [ref, ver])
    fail("NOT_FOUND", "Release not found", 404) unless release
    fail("QUARANTINED", "Release quarantined", 423) if release[:quarantined] == 1
    data = Json.parse(release[:common_json])
    data[:schema] = "sputnik.registry.release.v1"
    data[:package_ref] = ref
    data[:version] = ver
    data[:published_at] = timestamp(release[:published_at])
    data[:yanked] = release[:yanked] == 1
    data[:yank_reason] = release[:yank_reason]
    data[:artifacts] = conn.query("SELECT variant,metadata,quarantined FROM artifacts WHERE ref=? AND version=? ORDER BY variant", params: [ref, ver]).map |a|:
      metadata = Json.parse(a[:metadata])
      domain.compact_artifact(metadata, a[:quarantined] == 1)
    data

  def package_route(request, actor, ref, tail):
    read = request.method == "GET" or request.method == "HEAD"
    if read:
      return @store.db |conn|:
        requested = ref
        ref = conn.scalar("SELECT ref FROM root_aliases WHERE name=?", params: [ref]) or ref
        pkg = conn.query_one("SELECT * FROM packages WHERE ref=?", params: [ref])
        unless pkg:
          fail("NOT_FOUND", "Package not found", 404) if domain.scoped(requested)
          suffix = "/" + requested
          matches = conn.query("SELECT ref FROM packages WHERE substr(ref,-length(?))=? AND EXISTS(SELECT 1 FROM releases WHERE releases.ref=packages.ref AND quarantined=0 AND yanked=0) ORDER BY ref LIMIT 20", params: [suffix, suffix])
          suggestions = matches.map |row|: row[:ref]
          fail("NOT_FOUND", "Package not found", 404, suggestions)
        if tail.empty?:
          next response({package_ref: ref, requested_ref: requested, revision: pkg[:revision], owners: conn.query("SELECT account_id,role FROM owners WHERE ref=?", params: [ref])})
        if tail.count == 1 and tail[0] == "index":
          last = cursor(request, "index:" + ref, pkg[:revision])
          size = page_size(request, 100)
          after = if last == "" then "~" else version_key(last)
          rows = conn.query("SELECT version FROM releases WHERE ref=? AND quarantined=0 AND sort_key<? ORDER BY sort_key DESC LIMIT ?", params: [ref, after, size + 1])
          more = rows.count > size
          rows = rows[0...size] if more
          entries = []
          bytes = 0
          row_index = 0
          while row_index < rows.count:
            row = rows[row_index]
            row_index += 1
            data = release_data(conn, ref, row[:version])
            bytes += canonical(data).bytes.count
            if bytes > 1000000 and not entries.empty?:
              more = true
              break
            entries.push!(data)
          next response({schema: "sputnik.registry.index.v1", package_ref: ref, revision: pkg[:revision], versions: entries, next_cursor: if more then next_cursor("index:" + ref, pkg[:revision], entries[entries.count - 1][:version]) else null})
        if tail.count >= 2 and tail[0] == "versions":
          ver = version(tail[1])
          release = release_data(conn, ref, ver)
          if tail.count == 2: next response(release)
          if tail.count >= 4 and tail[2] == "artifacts":
            var = variant(tail[3])
            artifact = conn.query_one("SELECT * FROM artifacts WHERE ref=? AND version=? AND variant=?", params: [ref, ver, var])
            fail("NOT_FOUND", "Artifact not found", 404) unless artifact
            fail("QUARANTINED", "Artifact quarantined", 423) if artifact[:quarantined] == 1
            if tail.count == 4: next response(Json.parse(artifact[:metadata]))
            if tail.count == 5 and tail[4] == "download": next download(request, artifact)
        if tail.count == 2 and tail[0] == "stats":
          if tail[1] == "publications":
            fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
            @store.owner(conn, actor, ref)
            require_scope(actor, "package:stats:" + ref)
          next statistics(conn, ref, tail[1], request)
        fail("NOT_FOUND", "Unknown package endpoint", 404)
    fail("UNAUTHORIZED", "Bearer token required", 401) unless actor
    @store.db |conn|:
      fail("ROOT_ALIAS_READ_ONLY", "Use the canonical scoped reference", 409) if conn.scalar("SELECT 1 FROM root_aliases WHERE name=?", params: [ref])
    if tail.count == 3 and tail[0] == "versions" and ["yank", "unyank"].contains?(tail[2]) and request.method == "POST":
      return yank(request, actor, ref, version(tail[1]), tail[2] == "yank")
    if tail.count >= 1 and tail[0] == "owners": return owners(request, actor, ref, tail)
    fail("NOT_FOUND", "Unknown package write endpoint", 404)

  def download(request, artifact):
    hash = artifact[:digest]
    headers = {"content-type": "application/vnd.sputnik.package.v1", "cache-control": "no-store", "etag": "\"" + hash + "\"", "x-sputnik-sha256": "sha256:" + hash, "accept-ranges": "none", "content-disposition": "attachment; filename=\"" + artifact[:ref].replace("/", "-") + "-" + artifact[:version] + "-" + artifact[:variant] + ".sputnikpkg\""}
    # Open before sending headers: missing/corrupt storage must fail as an error.
    file = null
    try:
      file = storage.File(@store.root, hash, "read", 0)
      fail("STORAGE_UNAVAILABLE", "Artifact storage failed integrity check", 503) unless file.valid?(hash, artifact[:size])
    rescue IOError:
      fail("STORAGE_UNAVAILABLE", "Artifact storage is unavailable", 503)
    ensure:
      file.destroy!() if file
    if request.header("if-none-match") == headers["etag"]:
      return orbit.Response(status: 304, headers: headers, body: "")
    root = @store.root
    observer = |success, bytes|:
      completed(root, artifact, success, bytes)
    orbit.stream(headers: headers, length: artifact[:size], on_complete: &observer) |body|:
      input = storage.File(@store.root, hash, "read", 0)
      try:
        while bytes = input.read(): body.write(bytes)
      ensure:
        input.destroy!()

  def yank(request, actor, ref, ver, yanked):
    require_scope(actor, "package:yank:" + ref)
    @store.db |conn|:
      @store.owner(conn, actor, ref, strict: true)
    data = body(request, if yanked then ["reason"] else [])
    reason = data[?"reason"]
    fail("INVALID_REASON", "Reason exceeds 512 bytes") if reason and (not (Str === reason) or reason.bytes.count > 512)
    result = @store.write(actor, "yank:" + ref + ":" + ver + ":" + yanked.to_str, request.header("idempotency-key"), data) |conn, current|:
      @store.owner(conn, current, ref, strict: true)
      release_data(conn, ref, ver)
      conn.execute("UPDATE releases SET yanked=?,yank_reason=? WHERE ref=? AND version=?", params: [(if yanked then 1 else 0), reason, ref, ver])
      conn.execute("UPDATE packages SET revision=revision+1 WHERE ref=?", params: [ref])
      conn.execute("UPDATE catalog SET revision=revision+1 WHERE id=1")
      @store.audit(conn, if yanked then "release_yanked" else "release_unyanked", current[:account_id], ref, ver)
      [200, {package_ref: ref, version: ver, yanked: yanked}]
    response(result[1], result[0])

  def owners(request, actor, ref, tail):
    require_scope(actor, "package:owners:" + ref)
    @store.db |conn|:
      @store.owner(conn, actor, ref, strict: true)
    data = if request.method == "POST" then body(request, ["account_id", "role"], ["account_id", "role"]) else body(request, [])
    target = if request.method == "POST" then data[:account_id] else tail[?1]
    fail("INVALID_REQUEST", "Invalid ownership action") unless (request.method == "POST" and tail.count == 1) or (request.method == "DELETE" and tail.count == 2)
    fail("INVALID_ACCOUNT", "Expected account identifier") unless Str === target and target.length == 32 and target.contains_only?("0123456789abcdef")
    if request.method == "POST":
      fail("INVALID_ROLE", "Expected owner or publisher") unless ["owner", "publisher"].contains?(data[:role])
    result = @store.write(actor, "owners:" + ref + ":" + target, request.header("idempotency-key"), data) |conn, current|:
      @store.owner(conn, current, ref, strict: true)
      if request.method == "POST":
        @store.account(conn, target)
        fail("INVALID_ROLE", "Expected owner or publisher") unless ["owner", "publisher"].contains?(data[:role])
        conn.execute("INSERT INTO owners VALUES(?,?,?) ON CONFLICT(ref,account_id) DO UPDATE SET role=excluded.role", params: [ref, target, data[:role]])
      else:
        conn.execute("DELETE FROM owners WHERE ref=? AND account_id=?", params: [ref, target])
      fail("LAST_OWNER", "Package must retain an active owner", 409) unless conn.scalar("SELECT 1 FROM owners o JOIN accounts a ON a.id=o.account_id WHERE o.ref=? AND o.role='owner' AND a.active=1", params: [ref])
      @store.audit(conn, "owners_changed", current[:account_id], ref)
      [200, {owners: conn.query("SELECT account_id,role FROM owners WHERE ref=?", params: [ref])}]
    response(result[1], result[0])

  def statistics(conn, ref, kind, request):
    fail("NOT_FOUND", "Unknown statistics", 404) unless ["downloads", "publications"].contains?(kind)
    now = storage.now()
    today = now - now % 86400
    start_at = today - 29 * 86400
    to = today + 86400
    start_at = domain.date_seconds(request.query("from")) if request.query("from")
    to = domain.date_seconds(request.query("to")) if request.query("to")
    fail("INVALID_DATE_RANGE", "Statistics interval must be 1 to 366 days") unless to > start_at and to - start_at <= 366 * 86400
    ver = request.query("version")
    var = request.query("variant")
    version(ver) if ver
    variant(var) if var
    release_data(conn, ref, ver) if ver
    if var:
      artifact = conn.query_one("SELECT min(max(a.quarantined,r.quarantined)) AS quarantined FROM artifacts a JOIN releases r ON r.ref=a.ref AND r.version=a.version WHERE a.ref=? AND (? IS NULL OR a.version=?) AND a.variant=? HAVING count(*)>0", params: [ref, ver, ver, var])
      fail("NOT_FOUND", "Artifact not found", 404) unless artifact
      fail("QUARANTINED", "Artifact quarantined", 423) if artifact[:quarantined] == 1
    usage = Usage(@store)
    try:
      response(usage.stats(ref, kind, start_at, to, ver, var))
    ensure:
      usage.close()
