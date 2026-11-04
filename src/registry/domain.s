package registry.domain

import registry.storage as storage

export Error, fail, ident, sha, timestamp, reference, version, variant, profile, scoped, scope_allows, require_scope, validate_scopes, canonical, limits
export version_key, compact_artifact, expiry_seconds, date_seconds, cursor_text, validate_dependencies

class Error < Exception:
  attr message
  attr code
  attr status
  attr suggestions
  def init(@code, @message, @status = 400, @suggestions = null)

def fail(code, message, status = 400, suggestions = null):
  raise Error(code, message, status, suggestions)

def ident(): SecureRandom.hex(16)
def sha(value): Digest.sha256(value.bytes).hex()
def timestamp(value): Time.from_unix(value).iso8601
def canonical(value): Json.generate(value)

def scoped(ref): ref.contains?("/")

def reference(value):
  fail("INVALID_REFERENCE", "Invalid package reference") unless Str === value and value.length <= 128
  parts = value.split("/")
  fail("INVALID_REFERENCE", "Expected name or publisher/name") unless parts.count == 1 or parts.count == 2
  if parts.count == 2:
    slug = parts[0]
    fail("INVALID_REFERENCE", "Invalid publisher") unless slug.length > 0 and slug.length <= 63 and slug.contains_only?("abcdefghijklmnopqrstuvwxyz0123456789_") and "abcdefghijklmnopqrstuvwxyz".contains?(slug.slice(0...1))
  name = parts[parts.count - 1]
  valid = name.length > 0 and name.length <= 64 and "abcdefghijklmnopqrstuvwxyz".contains?(name.slice(0...1)) and name.contains_only?("abcdefghijklmnopqrstuvwxyz0123456789_.-")
  segments = name.replace("-", ".").split(".")
  valid = valid and segments.all? |part|:
    part.length > 0
  fail("INVALID_REFERENCE", "Invalid package name") unless valid
  value

def version(value):
  fail("INVALID_VERSION", "Invalid version") unless Str === value and value.length > 0 and value.length <= 64 and value.contains_only?("0123456789abcdefghijklmnopqrstuvwxyz.-")
  parts = value.split("-")
  release = parts[0].split(".")
  fail("INVALID_VERSION", "Expected major.minor.patch") unless release.count == 3
  release.each |n|:
    fail("INVALID_VERSION", "Invalid numeric version") unless n.length > 0 and n.contains_only?("0123456789") and (n == "0" or not n.starts_with?("0"))
  if value.contains?("-"):
    suffix = value.slice((parts[0].length + 1)...value.length)
    suffix.split(".").each |n|:
      fail("INVALID_VERSION", "Invalid prerelease") if n.empty? or (n.contains_only?("0123456789") and n.length > 1 and n.starts_with?("0"))
  value

def variant(value):
  fail("INVALID_VARIANT", "Invalid artifact variant") unless Str === value and value.length > 0 and value.length <= 128 and value.contains_only?("abcdefghijklmnopqrstuvwxyz0123456789_.-") and "abcdefghijklmnopqrstuvwxyz0123456789".contains?(value.slice(0...1)) and not value.contains?("..")
  value

def compact_artifact(metadata, quarantined = false):
  {variant: metadata[:variant], kind: metadata[:kind], bundle: metadata[:bundle], target: metadata[:target], native_mode: metadata[:native_mode], native_payload: metadata[?"native_payload"] or false, dependencies: metadata[:dependencies], build_dependencies: metadata[:build_dependencies], compatibility: metadata[:compatibility], install_options: metadata[?:install_options] or [], sha256: metadata[:sha256], size: metadata[:size], url: metadata[:url], quarantined: quarantined}

def validate_dependencies(common, runtime, build):
  fail("METADATA_LIMIT", "Too many dependencies") if common.count + runtime.count + build.count > 256
  versions = {}
  [common, runtime, build].each |group|:
    group.each |dep|:
      ref = reference(dep[?"package_ref"] or dep[?"name"])
      ver = version(dep[:version])
      fail("INVALID_DEPENDENCY", "Network dependencies must use registry source") unless dep[:source] == "registry"
      fail("DEPENDENCY_CONFLICT", "Exact dependency versions conflict") if versions.has_key?(ref) and versions[ref] != ver
      versions.store!(ref, ver)

def date_seconds(value):
  fail("INVALID_DATE", "Expected UTC dates YYYY-MM-DD") unless Str === value and value.length == 10
  try:
    expiry_seconds(value + "T00:00:00Z")
  rescue Error:
    fail("INVALID_DATE", "Expected UTC dates YYYY-MM-DD")

def expiry_seconds(value):
  fail("INVALID_EXPIRY", "Expected RFC3339 expiry") unless Str === value and value.length >= 20 and value.length <= 35
  zone = if value.ends_with?("Z") then value.length - 1 else value.length - 6
  fail("INVALID_EXPIRY", "Expected RFC3339 expiry") unless zone >= 19
  fail("INVALID_EXPIRY", "Expected RFC3339 expiry") unless value.slice(4...5) == "-" and value.slice(7...8) == "-" and value.slice(10...11) == "T" and value.slice(13...14) == ":" and value.slice(16...17) == ":"
  fields = [[0,4], [5,7], [8,10], [11,13], [14,16], [17,19]].map |range|:
    text = value.slice(range[0]...range[1])
    fail("INVALID_EXPIRY", "Expected RFC3339 expiry") unless text.contains_only?("0123456789")
    text.to_int
  year = fields[0]
  month = fields[1]
  day = fields[2]
  fail("INVALID_EXPIRY", "Invalid expiry date") unless year > 0 and month >= 1 and month <= 12
  days = [31,28,31,30,31,30,31,31,30,31,30,31]
  days[1] = 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
  fail("INVALID_EXPIRY", "Invalid expiry date") unless day >= 1 and day <= days[month - 1] and fields[3] <= 23 and fields[4] <= 59 and fields[5] <= 59
  if zone > 19:
    fraction = value.slice(20...zone)
    fail("INVALID_EXPIRY", "Invalid expiry fraction") unless value.slice(19...20) == "." and fraction.length > 0 and fraction.length <= 9 and fraction.contains_only?("0123456789")
  unless value.ends_with?("Z"):
    sign = value.slice(zone...(zone + 1))
    hours = value.slice((zone + 1)...(zone + 3))
    minutes = value.slice((zone + 4)...value.length)
    fail("INVALID_EXPIRY", "Invalid expiry offset") unless ["+", "-"].contains?(sign) and value.slice((zone + 3)...(zone + 4)) == ":" and hours.contains_only?("0123456789") and minutes.contains_only?("0123456789") and hours.to_int <= 23 and minutes.to_int <= 59
  Time.parse(value).unix_seconds

def cursor_text(value):
  alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
  fail("INVALID_CURSOR", "Invalid cursor encoding") unless value.length > 0 and value.length % 4 == 0
  bare = value
  padding = 0
  while bare.ends_with?("="):
    padding += 1
    bare = bare.slice(0...(bare.length - 1))
  fail("INVALID_CURSOR", "Invalid cursor encoding") unless padding <= 2 and not bare.empty? and bare.contains_only?(alphabet)
  last = bare.slice((bare.length - 1)...bare.length)
  fail("INVALID_CURSOR", "Invalid cursor padding") if (padding == 2 and not "AQgw".contains?(last)) or (padding == 1 and not "AEIMQUYcgkosw048".contains?(last))
  Base64.decode(value).to_str

def numeric_key(value):
  size = value.length.to_str
  size = "0" + size if size.length == 1
  size + value

def version_key(value):
  version(value)
  main = value.split("-")[0]
  components = main.split(".").map |part|:
    numeric_key(part)
  result = components.join("!")
  return result + "!2" unless value.contains?("-")
  suffix = value.slice((main.length + 1)...value.length)
  prerelease = suffix.split(".").map |part|:
    if part.contains_only?("0123456789") then "0" + numeric_key(part) else "1" + part
  result + "!1!" + prerelease.join("!")

def limits():
  {
    "standard": {artifact_bytes: 67108864, expanded_tar_bytes: 268435456, member_bytes: 67108864, upload_seconds: 600, session_seconds: 3600, pending_global: 536870912, pending_account: 201326592, traffic_bytes: 1073741824, storage_bytes: 10737418240},
    "native-large": {artifact_bytes: 2147483648, expanded_tar_bytes: 8589934592, member_bytes: 4294967296, upload_seconds: 7200, session_seconds: 14400, pending_global: 8589934592, pending_account: 4294967296, traffic_bytes: 10737418240, storage_bytes: 107374182400}
  }.freeze

def profile(name):
  result = limits()[?name]
  fail("INVALID_PROFILE", "Unknown limit profile") unless result
  result

def scope_allows(scopes, needed):
  return true if scopes.contains?(needed)
  if needed.starts_with?("package:publish:"):
    ref = needed.slice(16...needed.length)
    if scoped(ref):
      return scopes.contains?("namespace:publish:" + ref.split("/")[0])
  false

def require_scope(actor, needed):
  fail("FORBIDDEN", "Required token scope is missing", 403) unless scope_allows(actor[:scopes], needed)

def validate_scopes(scopes):
  fail("INVALID_SCOPES", "Expected at most 128 scopes") unless Array === scopes and scopes.count > 0 and scopes.count <= 128
  scopes.each |scope|:
    fail("INVALID_SCOPES", "Invalid scope") unless Str === scope and scope.length < 200
    if ["registry:read", "token:manage", "publisher-key:manage"].contains?(scope):
      next
    parts = scope.split(":")
    fail("INVALID_SCOPES", "Invalid scope") unless parts.count == 3
    if parts[0] == "namespace" and ["create-package", "publish"].contains?(parts[1]):
      reference(parts[2] + "/validation")
    elif parts[0] == "package" and ["publish", "yank", "owners", "stats"].contains?(parts[1]):
      reference(parts[2])
    else:
      fail("INVALID_SCOPES", "Unknown scope")
  scopes
