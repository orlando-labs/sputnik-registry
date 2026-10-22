package registry.storage_selftest

import registry.storage as storage
import sdk.fs as safe_fs

export main

def check(value, message):
  raise ValueError(message) unless value

def main():
  parser = ArgParser()
  parser.pos("directory")
  path = parser.parse_or_raise()[:directory]
  storage.storage_init(path)
  directory = safe_fs.Root(path)
  try:
    event = "{\"event\":1}"
    check(storage.spool_append(path, event), "append event")
    check(storage.spool_read(path) == event + "\n", "read batch")
    check(storage.spool_read(path) == event + "\n", "unacknowledged batch is replayed")
    storage.spool_ack(path)
    check(storage.spool_read(path) == "", "ack removes batch")
    file = directory.open("spool/events", mode: :append, create: true)
    try:
      file.write((event + "\n{\"torn\"").bytes)
      file.sync()
    ensure:
      file.close()
    check(storage.spool_read(path) == event + "\n", "torn tail is truncated")
    check(storage.spool_gap(path), "torn tail records coverage gap")
    storage.spool_ack(path)
    file = directory.open("spool/events", mode: :write, create: true)
    try:
      file.truncate(16777216)
      file.sync()
    ensure:
      file.close()
    check(not storage.spool_append(path, event), "bounded spool rejects overflow")
    directory.remove("spool/events")
    payload = "payload".bytes
    hash = Digest.sha256(payload).hex()
    upload = storage.File(path, "test", "write", payload.count)
    try:
      upload.write(payload)
      check(upload.finish() == hash, "streamed upload hash")
    ensure:
      upload.destroy!()
    storage.promote(path, "test", hash)
    storage.promote(path, "test", hash)
    stored = storage.File(path, hash, "read", 0)
    try:
      check(stored.read().to_str == payload.to_str, "CAS exact bytes after repeated promotion")
      check(stored.read() == null, "CAS EOF")
    ensure:
      stored.destroy!()
    blob = "blobs/" + hash.slice(0...2) + "/" + hash
    check(directory.metadata(blob)[:permissions] == 256, "CAS file is read-only")
    directory.remove(blob)
    changed = directory.open(blob, mode: :write, create: true, exclusive: true)
    try:
      changed.write("corrupt".bytes)
    ensure:
      changed.close()
    rejected = false
    try:
      storage.promote(path, "test", hash)
    rescue:
      rejected = true
    check(rejected, "existing corrupt CAS blob is rejected")
    true
  ensure:
    directory.close()
