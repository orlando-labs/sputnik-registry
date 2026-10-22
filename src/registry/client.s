package registry.client

from net.http import Client, RequestBody
import registry.storage as storage
from registry.domain import canonical, fail, reference, version, variant, profile, ident, scoped

export publish, fetch

class API:
  attr origin

  def init(args):
    @origin = args[:origin]
    fail("ORIGIN_REQUIRED", "--origin is required") unless Str === @origin and @origin.length > 0
    url = Url.parse(@origin)
    fail("INVALID_ORIGIN", "Expected canonical HTTPS origin or loopback HTTP") unless (@origin.starts_with?("https://") or @origin.starts_with?("http://127.0.0.1:") or @origin.starts_with?("http://localhost:")) and not @origin.ends_with?("/") and not @origin.contains?("@") and not @origin.contains?("?") and not @origin.contains?("#")
    authority = @origin.split("://")[1]
    fail("INVALID_ORIGIN", "Origin cannot include a path") if authority.contains?("/")
    credentials = Json.parse(storage.private_text(args[:credentials]), duplicate_keys: :reject)
    fail("ORIGIN_MISMATCH", "Credential origin differs") unless credentials[:origin] == @origin
    @token = credentials[:token]
    fail("INVALID_TOKEN", "Invalid credential token") unless Str === @token and @token.starts_with?("spreg_") and @token.length <= 256
    @http = Client(timeout: 30.0, redirects: :off)

  def close(): @http.close!()

  def headers(key = null):
    result = {"authorization": "Bearer " + @token, "content-type": "application/json"}
    result["idempotency-key"] = key if key
    result

  def document(response):
    text = response.body_text(limit: 1048576)
    fail("HTTP_ERROR", "Registry returned HTTP " + response.status.to_str) if response.status < 200 or response.status >= 300
    Json.parse(text, duplicate_keys: :reject)

  def get(path): document(@http.get(@origin + path, headers: headers()))
  def post(path, data, key): document(@http.post(@origin + path, headers: headers(key), body: canonical(data)))

  def upload(path, artifact, size):
    fs.File.open(artifact, :read) |file|:
      payload = RequestBody.from_reader(file, length: size)
      selected = headers()
      selected["content-type"] = "application/vnd.sputnik.package.v1"
      document(@http.put(@origin + path, headers: selected, body: payload))

  def download(path): @http.get(@origin + path, headers: headers())

def base(ref, ver, var):
  location = if scoped(ref) then "/namespaces/" + ref.split("/")[0] + "/packages/" + ref.split("/")[1] else "/packages/" + ref
  "/api/v1" + location + "/versions/" + ver + "/artifacts/" + var

def identity(args):
  reference(args[:ref])
  version(args[:version])
  variant(args[:variant])

def create_state(path, state):
  fs.File.open(path, :write, create: true, exclusive: true, permissions: 384) |file|:
    file.write_all!(canonical(state).bytes)
  fs.sync(path)
  fs.sync(fs.Path(storage.absolute(path)).parent)

def publish(args):
  identity(args)
  artifact = args[:artifact]
  fail("ARGUMENT_REQUIRED", "--artifact, --credentials and --session are required") unless Str === artifact and Str === args[:session] and Str === args[:credentials]
  path = args[:session]
  size = fs.metadata(artifact).size
  fail("INVALID_SIZE", "Artifact exceeds enabled upload profile") unless size > 0 and size <= profile(args[:profile])[:artifact_bytes]
  hash = storage.hash_file(artifact)
  api = API(args)
  try:
    declaration = {package_ref: args[:ref], version: args[:version], variant: args[:variant], expected_size: size, expected_sha256: "sha256:" + hash, limit_profile: args[:profile]}
    identity = {origin: api.origin, declaration: declaration}
    state = null
    if fs.exists?(path):
      state = Json.parse(storage.private_text(path), duplicate_keys: :reject)
      fail("SESSION_MISMATCH", "Session file belongs to another artifact or origin") unless canonical(state[:identity]) == canonical(identity)
    else:
      state = {identity: identity, create_key: ident(), commit_key: ident()}
      create_state(path, state)
    unless state.has_key?("id"):
      state[:id] = api.post("/api/v1/publish-sessions", declaration, state[:create_key])[:id]
      fs.atomic_write_text(path, canonical(state))
    session_path = "/api/v1/publish-sessions/" + state[:id]
    session = api.get(session_path)
    session = api.upload(session_path + "/artifact", artifact, size) if session[:state] == "created"
    session = api.post(session_path + "/commit", {}, state[:commit_key]) if session[:state] == "uploaded"
    started = Time.monotonic
    while ["validating", "promoting"].contains?(session[:state]):
      fail("PUBLISH_TIMEOUT", "Worker did not finish; retry with the same session file") if (Time.monotonic - started).nanoseconds > 1200000000000
      task.sleep(1.0)
      session = api.get(session_path)
    fail("PUBLICATION_FAILED", "Publication ended in state " + session[:state]) unless session[:state] == "published"
    session
  ensure:
    api.close()

def fetch(args):
  identity(args)
  output = args[:out]
  fail("ARGUMENT_REQUIRED", "--out and --credentials are required") unless Str === output and Str === args[:credentials]
  path = base(args[:ref], args[:version], args[:variant])
  api = API(args)
  temporary = output + ".partial-" + ident()
  try:
    location = if scoped(args[:ref]) then "/namespaces/" + args[:ref].split("/")[0] + "/packages/" + args[:ref].split("/")[1] else "/packages/" + args[:ref]
    resolved = reference(api.get("/api/v1" + location)[:package_ref])
    fail("IDENTITY_MISMATCH", "Invalid root alias") unless resolved == args[:ref] or (not scoped(args[:ref]) and scoped(resolved) and resolved.split("/")[1] == args[:ref])
    path = base(resolved, args[:version], args[:variant])
    metadata = api.get(path)
    fail("IDENTITY_MISMATCH", "Artifact metadata differs") unless metadata[:package_ref] == resolved and metadata[:version] == args[:version] and metadata[:variant] == args[:variant] and metadata[:url] == api.origin + path + "/download"
    size = metadata[:size]
    maximum = if args[:allow_native_large] then 2147483648 else 67108864
    fail("SIZE_LIMIT", "Artifact exceeds enabled download profile") unless Int === size and size > 0 and size <= maximum
    hash = metadata[:sha256]
    fail("INVALID_DIGEST", "Invalid artifact digest") unless Str === hash and hash.length == 71 and hash.starts_with?("sha256:") and hash.slice(7...71).contains_only?("0123456789abcdef")
    response = api.download(path + "/download")
    fail("INVALID_DOWNLOAD", "Download status, encoding or framing differs") unless response.status == 200 and response.headers.first("content-length") == size.to_str and response.headers.first("x-sputnik-sha256") == hash and response.headers.first("content-encoding") == null
    total = 0
    fs.File.open(temporary, :write, create: true, exclusive: true, permissions: 384) |file|:
      loop:
        bytes = response.body.read(max_bytes: 65536)
        if bytes.empty?:
          break
        total += bytes.count
        fail("SIZE_LIMIT", "Download exceeds declared size") if total > size
        file.write_all!(bytes)
    fail("DIGEST_MISMATCH", "Downloaded size or SHA-256 differs") unless total == size and "sha256:" + storage.hash_file(temporary) == hash
    fs.sync(temporary)
    storage.link_new(temporary, output)
    {artifact: storage.absolute(output), size: size, sha256: hash}
  ensure:
    api.close()
    fs.remove(temporary) if fs.exists?(temporary)
