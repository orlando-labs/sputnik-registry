package registry.auth

from net.http import Client
from sync import Mutex
import registry.storage as storage
from registry.domain import fail, sha, ident, timestamp, canonical, reference, validate_scopes, expiry_seconds

export DeviceLogin

class DeviceLogin:
  def init(@store):
    @mutex = Mutex.new()
    @codes = {}
    @github = "https://github.com"
    @api = "https://api.github.com"
    # A loopback-only fixture seam, never a remote provider override.
    fixture = storage.env("SPUTNIK_REGISTRY_TEST_GITHUB_ORIGIN")
    unless fixture.empty?:
      fail("INVALID_CONFIG", "Test provider requires loopback registry and provider") unless @store.config[:origin].starts_with?("http://127.0.0.1:") and fixture.starts_with?("http://127.0.0.1:") and not fixture.contains?("@") and not fixture.contains?("?") and not fixture.contains?("#") and not fixture.split("://")[1].contains?("/")
      @github = fixture
      @api = fixture

  def enabled?(): not @store.config[:github_client_id].empty?

  def provider(path, data = null, token = null):
    client = Client(timeout: 10.0, redirects: :off)
    try:
      headers = {"accept": "application/json", "user-agent": "Sputnik-Registry", "content-type": "application/json"}
      headers["authorization"] = "Bearer " + token if token
      response = if token then client.get(@api + path, headers: headers) else client.post(@github + path, headers: headers, body: canonical(data))
      fail("GITHUB_UNAVAILABLE", "GitHub login request failed", 502) unless response.status == 200
      result = Json.parse(response.body_text(limit: 65536), duplicate_keys: :reject)
      fail("GITHUB_UNAVAILABLE", "Invalid GitHub response", 502) unless Map === result or StrictMap === result
      result
    rescue Exception:
      fail("GITHUB_UNAVAILABLE", "GitHub login is temporarily unavailable", 502)
    ensure:
      client.close!()

  def forget(id):
    @mutex.synchronize: @codes.delete!(id)

  def start(data, ip):
    fail("LOGIN_DISABLED", "GitHub login is not configured", 503) unless enabled?
    fail("INVALID_PROVIDER", "Expected GitHub provider") unless data[:provider] == "github"
    expected = data[?"expected_account_id"]
    fail("INVALID_ACCOUNT", "Invalid expected account") if expected and (not (Str === expected) or expected.length != 32 or not expected.contains_only?("0123456789abcdef"))
    id = ident()
    secret = "splog_" + SecureRandom.hex(32)
    @store.transaction |conn|:
      conn.execute("DELETE FROM device_logins WHERE expires_at<?", params: [storage.now() - 3600])
      @store.rate(conn, "login-start:" + ip, 10)
      @store.rate(conn, "login-start-global", 60)
      fail("LOGIN_CAPACITY", "Too many active logins; try later", 429) if conn.scalar("SELECT count(*) FROM device_logins WHERE expires_at>? AND state IN ('starting','pending','verified')", params: [storage.now()]) >= 128
      conn.execute("INSERT INTO device_logins(id,secret_digest,state,expected_account_id,expires_at,created_at) VALUES(?,?,'starting',?,?,?)", params: [id, sha(secret), expected, storage.now() + 900, storage.now()])
    try:
      result = provider("/login/device/code", {client_id: @store.config[:github_client_id], scope: ""})
      code = result[?"device_code"]
      user = result[?"user_code"]
      ttl = result[?"expires_in"]
      interval = result[?"interval"]
      fail("GITHUB_UNAVAILABLE", "Invalid GitHub device response", 502) unless Str === code and code.length > 0 and code.length <= 256 and Str === user and user.length > 0 and user.length <= 32 and user.contains_only?("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-") and Int === ttl and ttl > 0 and Int === interval and interval >= 1 and interval <= 60 and result[:verification_uri] == @github + "/login/device"
      ttl = 900 if ttl > 900
      expiry = storage.now() + ttl
      @mutex.synchronize:
        expired = @codes.keys.select |key|: @codes[key][:expires_at] <= storage.now()
        expired.each |key|: @codes.delete!(key)
        @codes[id] = {code: code, expires_at: expiry}
      @store.transaction |conn|:
        conn.execute("UPDATE device_logins SET state='pending',expires_at=?,interval=?,next_poll=? WHERE id=?", params: [expiry, interval, storage.now() + interval, id])
      {attempt_id: id, login_secret: secret, user_code: user, verification_uri: result[:verification_uri], expires_at: timestamp(expiry), interval: interval}
    rescue Exception |error|:
      forget(id)
      @store.transaction |conn|:
        conn.execute("UPDATE device_logins SET state='failed',failure='GITHUB_UNAVAILABLE' WHERE id=?", params: [id])
      raise error

  def attempt(conn, id, header):
    fail("UNAUTHORIZED", "Login secret required", 401) unless Str === header and header.starts_with?("Bearer splog_") and header.length == 77
    row = conn.query_one("SELECT * FROM device_logins WHERE id=? AND secret_digest=?", params: [id, sha(header.slice(7...header.length))])
    fail("UNAUTHORIZED", "Invalid login secret", 401) unless row
    if row[:expires_at] <= storage.now():
      fail("LOGIN_EXPIRED", "Login expired; start again", 410)
    row

  def summary(conn, row):
    result = {state: row[:state], retry_after: row[:interval], expires_at: timestamp(row[:expires_at])}
    if row[:failure]: result[:error] = {code: row[:failure], message: "Login failed; start again"}
    if row[:state] == "verified":
      result[:identity] = {provider: "github", subject: row[:subject], login: row[:login]}
      account = conn.scalar("SELECT account_id FROM external_identities WHERE provider='github' AND subject=?", params: [row[:subject]])
      if account:
        @store.account(conn, account)
        result[:account_id] = account
        result[:namespace] = conn.scalar("SELECT slug FROM namespaces WHERE account_id=? AND active=1 ORDER BY slug LIMIT 1", params: [account])
    result

  def poll(id, header):
    fence = ident()
    claimed = @store.transaction |conn|:
      row = attempt(conn, id, header)
      unless row[:state] == "pending" and row[:next_poll] <= storage.now():
        next false
      @store.rate(conn, "login-provider-global", 120)
      conn.execute("UPDATE device_logins SET fence=?,next_poll=? WHERE id=?", params: [fence, storage.now() + 30 + row[:interval], id])
      true
    if claimed:
      try:
        local = @mutex.synchronize: @codes[?id]
        fail("LOGIN_RESTARTED", "Server restarted; start login again", 409) unless local
        result = provider("/login/oauth/access_token", {client_id: @store.config[:github_client_id], device_code: local[:code], grant_type: "urn:ietf:params:oauth:grant-type:device_code"})
        identity = null
        if result[?"access_token"]:
          token = result[:access_token]
          fail("GITHUB_UNAVAILABLE", "Invalid GitHub access token", 502) unless Str === token and token.length > 0 and token.length <= 512 and result[:token_type] == "bearer"
          identity = provider("/user", null, token)
          fail("GITHUB_UNAVAILABLE", "Invalid GitHub identity", 502) unless Int === identity[?"id"] and identity[:id] > 0 and Str === identity[?"login"] and identity[:login].length > 0 and identity[:login].length <= 128 and identity[:type] == "User"
        @store.transaction |conn|:
          row = attempt(conn, id, header)
          unless row[:state] == "pending" and row[:fence] == fence:
            next null
          state = "pending"
          failure = null
          interval = row[:interval]
          expiry = row[:expires_at]
          if identity:
            subject = identity[:id].to_str
            account = conn.scalar("SELECT account_id FROM external_identities WHERE provider='github' AND subject=?", params: [subject])
            state = "verified"
            if row[:expected_account_id] and row[:expected_account_id] != account:
              state = "failed"
              failure = "ACCOUNT_MISMATCH"
            elif not account and @store.config[:registration] != "open":
              state = "failed"
              failure = "REGISTRATION_CLOSED"
            elif account and not conn.scalar("SELECT 1 FROM accounts WHERE id=? AND active=1", params: [account]):
              state = "failed"
              failure = "ACCOUNT_SUSPENDED"
            expiry = storage.now() + 300
            conn.execute("UPDATE device_logins SET subject=?,login=? WHERE id=?", params: [subject, identity[:login], id])
          elif result[?"error"] == "slow_down":
            interval += 5
          elif result[?"error"] == "access_denied":
            state = "denied"
          elif result[?"error"] == "expired_token":
            state = "expired"
          elif result[?"error"] != "authorization_pending":
            state = "failed"
            failure = "GITHUB_UNAVAILABLE"
          conn.execute("UPDATE device_logins SET state=?,failure=?,interval=?,next_poll=?,expires_at=?,fence=NULL WHERE id=?", params: [state, failure, interval, storage.now() + interval, expiry, id])
          forget(id) unless state == "pending"
      rescue Exception |error|:
        @store.transaction |conn|:
          conn.execute("UPDATE device_logins SET state='failed',failure='GITHUB_UNAVAILABLE',fence=NULL WHERE id=? AND state='pending' AND fence=?", params: [id, fence])
        forget(id)
        raise error
    @store.db |conn|:
      summary(conn, attempt(conn, id, header))

  def complete(id, header, key, data):
    fail("IDEMPOTENCY_KEY_REQUIRED", "Completion requires Idempotency-Key") unless Str === key and key.length >= 16 and key.length <= 128
    @store.transaction |conn|:
      row = attempt(conn, id, header)
      fail("TOKEN_SECRET_ALREADY_ISSUED", "Token secret was already issued; cancel this attempt to revoke it", 409) if row[:state] == "completed"
      fail("LOGIN_NOT_VERIFIED", "GitHub identity has not been verified", 409) unless row[:state] == "verified"
      account = conn.scalar("SELECT account_id FROM external_identities WHERE provider='github' AND subject=?", params: [row[:subject]])
      fail("ACCOUNT_MISMATCH", "Verified account differs", 403) if row[:expected_account_id] and row[:expected_account_id] != account
      namespace = null
      if account:
        @store.account(conn, account)
        namespace = conn.scalar("SELECT slug FROM namespaces WHERE account_id=? AND active=1 ORDER BY slug LIMIT 1", params: [account])
        fail("NAMESPACE_UNAVAILABLE", "No active personal namespace", 403) unless namespace
        fail("NAMESPACE_MISMATCH", "Existing account keeps its namespace", 409) if data[?"namespace"] and data[:namespace] != namespace
      else:
        fail("REGISTRATION_CLOSED", "Self-service registration is closed", 403) unless @store.config[:registration] == "open"
        namespace = data[?"namespace"]
        reference(namespace + "/validation") if Str === namespace
        reserved = ["admin", "administrator", "root", "registry", "sputnik", "sputnik_lang", "support", "security", "system", "official"]
        fail("NAMESPACE_UNAVAILABLE", "Choose a free personal namespace", 409) unless Str === namespace and not reserved.contains?(namespace) and not conn.scalar("SELECT 1 FROM namespaces WHERE slug=?", params: [namespace])
        account = ident()
        conn.execute("INSERT INTO accounts(id,label,created_at) VALUES(?,?,?)", params: [account, row[:login], storage.now()])
        conn.execute("INSERT INTO namespaces(slug,account_id) VALUES(?,?)", params: [namespace, account])
        conn.execute("INSERT INTO external_identities VALUES('github',?,?,?,?)", params: [row[:subject], account, row[:login], storage.now()])
        @store.audit(conn, "github_account_created", account)
      options = data[?"token"] or {}
      fail("INVALID_TOKEN_OPTIONS", "Expected token options") unless Map === options or StrictMap === options
      options.each |k, v|:
        fail("UNKNOWN_FIELD", "Unknown token field") unless ["label", "scopes", "expires_at"].contains?(k.to_str)
      scopes = options[?"scopes"] or ["registry:read", "token:manage", "namespace:create-package:" + namespace, "namespace:publish:" + namespace]
      validate_scopes(scopes)
      scopes.each |scope|:
        if ["registry:read", "token:manage"].contains?(scope): next null
        parts = scope.split(":")
        allowed = false
        if parts[0] == "namespace":
          allowed = conn.scalar("SELECT 1 FROM namespaces WHERE slug=? AND account_id=? AND active=1", params: [parts[2], account]) != null
        elif parts[0] == "package":
          role = conn.scalar("SELECT role FROM owners WHERE ref=? AND account_id=?", params: [parts[2], account])
          allowed = role == "owner" or (role == "publisher" and parts[1] == "publish")
        fail("SCOPE_ESCALATION", "Requested scope exceeds account permissions", 403) unless allowed
      expiry = if options[?"expires_at"] then expiry_seconds(options[:expires_at]) else storage.now() + 2592000
      fail("INVALID_EXPIRY", "Token expiry must be within 90 days") unless expiry > storage.now() and expiry <= storage.now() + 7776000
      label = options[?"label"] or "Sputnik CLI"
      fail("INVALID_LABEL", "Invalid token label") unless Str === label and label.length > 0 and label.bytes.count <= 128
      result = @store.issue(conn, account, label, scopes, expiry)
      result[:account_id] = account
      result[:namespace] = namespace
      result[:identity] = {provider: "github", subject: row[:subject], login: row[:login]}
      conn.execute("UPDATE external_identities SET login=? WHERE provider='github' AND subject=?", params: [row[:login], row[:subject]])
      conn.execute("UPDATE device_logins SET state='completed',account_id=?,token_id=?,complete_key=?,complete_hash=? WHERE id=?", params: [account, result[:id], key, sha(canonical(data)), id])
      @store.audit(conn, "github_login", account)
      result

  def cancel(id, header):
    @store.transaction |conn|:
      row = attempt(conn, id, header)
      if row[:token_id]:
        conn.execute("UPDATE tokens SET revoked=1 WHERE id=?", params: [row[:token_id]])
      conn.execute("UPDATE device_logins SET state='cancelled',fence=NULL WHERE id=?", params: [id])
      forget(id)
      {cancelled: true}
