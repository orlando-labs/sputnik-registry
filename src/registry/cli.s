package registry.cli

import registry.storage as storage
from registry.domain import canonical, fail

export arguments, required, write_new, init_config, supervise, operator_input

def required(args, name):
  value = args[?name]
  fail("ARGUMENT_REQUIRED", "Required option: --" + name.replace("_", "-")) unless value != null and value != ""
  value

def arguments():
  parser = ArgParser(name: "Sputnik Registry")
  parser.pos("command", required: false, default: storage.env("SPUTNIK_REGISTRY_COMMAND"))
  parser.arg("--config", default: storage.env("SPUTNIK_REGISTRY_CONFIG"))
  ["data-dir", "migrations-dir", "label", "namespace", "input", "origin", "credentials", "artifact", "ref", "version", "variant", "session", "out", "backup"].each |name|:
    parser.arg("--" + name)
  parser.arg("--port", type: Int, default: 8080)
  parser.arg("--profile", default: "standard", choices: ["standard", "native-large"])
  parser.flag("--allow-native-large")
  parser.parse_or_raise()

def write_new(path, value):
  fs.File.open(path, :write, create: true, exclusive: true, permissions: 384) |file|:
    file.write_all!(canonical(value).bytes)
  fs.sync(path)
  fs.sync(fs.Path(storage.absolute(path)).parent)

def init_config(args):
  path = required(args, "config")
  port = args[:port]
  fail("INVALID_PORT", "Port must be 1 to 65535") unless port > 0 and port <= 65535
  config = {data_dir: storage.absolute(required(args, "data_dir")), origin: "http://127.0.0.1:" + port.to_str, host: "127.0.0.1", port: port, allowed_hosts: ["127.0.0.1", "localhost"], read_access: "authenticated", native_large: false, cursor_secret: SecureRandom.hex(32), migrations_dir: storage.absolute(args[:migrations_dir] or "migrations")}
  write_new(path, config)
  {config: storage.absolute(path)}

def operator_input(args):
  if args[:input]:
    return Json.parse(fs.read_text(args[:input], limit: 65536), duplicate_keys: :reject)
  text = storage.env("SPUTNIK_REGISTRY_INPUT")
  text = storage.stdin_text() if text.empty?
  Json.parse(text, duplicate_keys: :reject)

def supervise(args):
  config = storage.absolute(required(args, "config"))
  storage.signals()
  while not storage.stopping():
    command = system.command(storage.executable(), "--config", config, "worker-once", group: "new")
    try:
      command.spawn(timeout: 960.0) |process|:
        while process.running?():
          if storage.stopping():
            process.terminate()
            break
          task.sleep(0.1)
        result = process.communicate(check: false, limit: 1048576)
        if result.success?:
          data = Json.parse(result.stdout.to_str)
          io.stdout.write_line(canonical(data)) if data[:processed]
        else:
          io.stderr.write_line("worker failed; durable lease will be retried")
    rescue ProcessTimeoutError:
      io.stderr.write_line("worker deadline exceeded; durable lease will be retried")
    task.sleep(0.5) unless storage.stopping()
  {stopped: true}
