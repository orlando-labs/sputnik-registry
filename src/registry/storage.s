package registry.storage

import sdk.fs as safe_fs
import sdk.digest as streaming_digest
import sdk.process as process
import sdk.pkg as pkg
import sqlite3

export env, now, storage_init, remove, inspect, promote, File, staging_keys
export spool_append, spool_read, spool_ack, spool_gap, spool_mark_gap
export harden, absolute, executable, signals, stopping, worker_limits, hash_file, private_text, copy_new, link_new, sqlite_snapshot, stdin_text

def env(name): process.env(name)
def now(): Time.now.unix_seconds
def harden(): process.umask(63)
def absolute(path): safe_fs.absolute(path)
def executable(): process.executable()
def signals(): process.trap_termination()
def stopping(): process.termination_requested()
# Linux runtime thread pools reserve virtual stacks before using physical pages.
# Deployment separately bounds worker RSS with the container memory limit.
def worker_limits(): process.limits(core: 0, files: 1024, address_space: 2147483648)
def stdin_text(): process.stdin_text(limit: 65536)

def component(key):
  raise ValueError("Invalid storage key") unless Str === key and key.length > 0 and key.length <= 128 and key.contains_only?("abcdefghijklmnopqrstuvwxyz0123456789_-")
  key

def digest(key):
  raise ValueError("Invalid digest") unless Str === key and key.length == 64 and key.contains_only?("0123456789abcdef")
  key

def blob_path(hash):
  digest(hash)
  "blobs/" + hash.slice(0...2) + "/" + hash

def root(path): safe_fs.Root(path, create: true)

def path_file(path, mode = :read, create = false, exclusive = false):
  directory = safe_fs.Root(fs.Path(absolute(path)).parent.to_str)
  try:
    directory.open(fs.Path(path).basename.to_str, mode: mode, create: create, exclusive: exclusive)
  ensure:
    directory.close()

def hash_file(path):
  file = path_file(path)
  try:
    file.sha256()
  ensure:
    file.close()

def private_text(path):
  file = path_file(path)
  try:
    metadata = file.metadata()
    raise ValueError("Credentials must be owned by this user, regular and private") unless metadata[:uid] == process.uid() and (metadata[:permissions] & 63) == 0 and metadata[:size] <= 65536
    result = ""
    while bytes = file.read():
      result += bytes.to_str
      raise ValueError("Credentials exceed 64 KiB") if result.bytes.count > 65536
    result
  ensure:
    file.close()

def copy_new(source, target):
  input = path_file(source)
  output = null
  total = 0
  try:
    output = path_file(target, :write, true, true)
    while bytes = input.read():
      output.write(bytes)
      total += bytes.count
    output.sync()
    fs.sync(fs.Path(absolute(target)).parent)
    total
  ensure:
    input.close()
    output.close() if output

def link_new(source, target):
  directory = safe_fs.Root("/")
  try:
    raise ValueError("Output already exists") unless directory.link(absolute(source).slice(1...absolute(source).length), absolute(target).slice(1...absolute(target).length))
  ensure:
    directory.close()

def sqlite_snapshot(source, target):
  # Securely create the destination before SQLite opens the named file.
  input = path_file(source)
  output = null
  try:
    output = path_file(target, :read_write, true, true)
    sqlite3.open(source, mode: :readonly) |live|:
      sqlite3.open(target, mode: :readwrite) |snapshot|:
        live.backup_into(snapshot)
    output.sync()
    fs.sync(fs.Path(absolute(target)).parent)
  ensure:
    input.close()
    output.close() if output

def storage_init(path):
  directory = root(path)
  try:
    directory.mkdir("staging")
    directory.mkdir("blobs")
    directory.mkdir("spool")
    ["metadata.sqlite", "usage.sqlite"].each |name|:
      file = directory.open(name, mode: :read_write, create: true)
      file.close()
  ensure:
    directory.close()

class File:
  def init(path, key, mode, @maximum):
    @size = 0
    @hash = null
    @finished = false
    @directory = root(path)
    try:
      if mode == "write":
        @file = @directory.open("staging/" + component(key), mode: :write, create: true, exclusive: true)
        @hash = streaming_digest.SHA256()
      elif mode == "read":
        @file = @directory.open(blob_path(key))
      elif mode == "lock":
        @file = @directory.lock(component(key) + ".lock", nonblock: true)
      else:
        raise ValueError("Invalid file mode")
    rescue |error|:
      @directory.close()
      raise error

  def write(bytes):
    raise ValueError("Artifact write exceeds size limit") if @finished or bytes.count > @maximum - @size
    @file.write(bytes)
    @hash.update(bytes)
    @size += bytes.count
    @size

  def finish():
    raise ValueError("Already finished") if @finished
    @file.sync()
    @directory.sync("staging")
    @finished = true
    @hash.finish()

  def read(): @file.read()
  def valid?(hash, size): @file.metadata()[:size] == size and @file.sha256() == hash
  def destroy!():
    @file.close()
    @hash.destroy!() if @hash
    @directory.close()

def remove(path, key):
  directory = root(path)
  try:
    directory.remove("staging/" + component(key))
  ensure:
    directory.close()

def staging_keys(path):
  directory = root(path)
  try:
    entries = []
    directory.entries("staging", limit: 1000).each |key|:
      if key.length == 32 and key.contains_only?("0123456789abcdef"):
        metadata = directory.metadata("staging/" + key)
        entries.push!([key, metadata[:mtime]]) if metadata and metadata[:file]
    entries
  ensure:
    directory.close()

def inspect(path, key, profile):
  directory = root(path)
  file = null
  try:
    file = directory.open("staging/" + component(key))
    facts = pkg.inspect(file, required_profile: profile)
    manifest = facts[:manifest]
    mode = "none"
    manifest[:native_kinds].each |kind|:
      mode = if mode == "none" then kind else (if mode == kind then mode else "mixed")
    dependencies = manifest[:artifact_dependencies].map |row|:
      {package_ref: row[0], version: row[1], source: row[2]}
    build_dependencies = manifest[:artifact_build_dependencies].map |row|:
      {package_ref: row[0], version: row[1], source: row[2]}
    representations = manifest[:module_representations].map |row|:
      {name: row[0], representation: row[1], path: row[2], sha256: "sha256:" + row[3]}
    descriptor = {limit_profile: manifest[:limit_profile], kind: manifest[:kind], bundle: manifest[:bundle], dependencies: dependencies, build_dependencies: build_dependencies, target: manifest[:target], compatibility: facts[:compatibility], module_representations: representations, native_mode: mode, native_payload: manifest[:native_payload]}
    descriptor[:install_options] = manifest[?:install_options] or []
    Json.generate({distribution: facts[:distribution], manifest: manifest, descriptor: descriptor})
  ensure:
    file.close() if file
    directory.close()

def promote(path, key, hash):
  directory = root(path)
  source = "staging/" + component(key)
  destination = blob_path(hash)
  file = null
  existing = null
  try:
    file = directory.open(source)
    raise ValueError("Staging digest changed") unless file.sha256() == hash
    file.sync()
    directory.chmod(source, 256)
    directory.mkdir("blobs/" + hash.slice(0...2))
    unless directory.link(source, destination):
      existing = directory.open(destination)
      raise ValueError("CAS corruption") unless existing.metadata()[:size] == file.metadata()[:size] and existing.sha256() == hash
    directory.sync("blobs/" + hash.slice(0...2))
    directory.sync("blobs")
  ensure:
    file.close() if file
    existing.close() if existing
    directory.close()

def gap(directory):
  marker = directory.open("spool/gap", mode: :write, create: true)
  try:
    marker.sync()
    directory.sync("spool")
  ensure:
    marker.close()

def spool_append(path, event):
  raise ValueError("Spool event exceeds 8192 bytes") if event.bytes.count + 1 > 8192
  directory = root(path)
  lock = null
  file = null
  try:
    directory.mkdir("spool")
    lock = directory.lock("spool/lock")
    file = directory.open("spool/events", mode: :append, create: true)
    if file.metadata()[:size] + event.bytes.count + 1 > 16777216:
      gap(directory)
      return false
    file.write((event + "\n").bytes)
    file.sync()
    directory.sync("spool")
    true
  rescue Exception |error|:
    gap(directory)
    raise error
  ensure:
    file.close() if file
    lock.close() if lock
    directory.close()

def spool_mark_gap(path):
  directory = root(path)
  try:
    gap(directory)
  ensure:
    directory.close()

def spool_read(path):
  directory = root(path)
  lock = null
  file = null
  try:
    directory.mkdir("spool")
    lock = directory.lock("spool/lock")
    unless directory.metadata("spool/batch"):
      directory.rename("spool/events", "spool/batch") if directory.metadata("spool/events")
    return "" unless directory.metadata("spool/batch")
    file = directory.open("spool/batch", mode: :read_write)
    raise ValueError("Spool batch exceeds limit") if file.metadata()[:size] > 16777216
    data = ""
    while bytes = file.read(): data += bytes.to_str
    unless data.empty? or data.ends_with?("\n"):
      lines = data.split("\n")
      lines.pop!()
      data = if lines.empty? then "" else lines.join("\n") + "\n"
      file.truncate(data.bytes.count)
      file.sync()
      gap(directory)
    data
  ensure:
    file.close() if file
    lock.close() if lock
    directory.close()

def spool_ack(path):
  directory = root(path)
  try:
    directory.remove("spool/batch")
  ensure:
    directory.close()

def spool_gap(path):
  directory = root(path)
  try:
    directory.metadata("spool/gap") != null
  ensure:
    directory.close()
