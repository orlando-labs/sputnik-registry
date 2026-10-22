CREATE TABLE external_identities (
  provider TEXT NOT NULL,
  subject TEXT NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  login TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(provider,subject)
);
CREATE INDEX identity_account ON external_identities(account_id);
-- No GitHub device/access token or recoverable login secret is persisted.
CREATE TABLE device_logins (
  id TEXT PRIMARY KEY,
  secret_digest TEXT NOT NULL,
  state TEXT NOT NULL,
  expected_account_id TEXT,
  subject TEXT,
  login TEXT,
  account_id TEXT REFERENCES accounts(id),
  token_id TEXT REFERENCES tokens(id),
  expires_at INTEGER NOT NULL,
  interval INTEGER NOT NULL DEFAULT 5,
  next_poll INTEGER NOT NULL DEFAULT 0,
  fence TEXT,
  failure TEXT,
  complete_key TEXT,
  complete_hash TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX login_expiry ON device_logins(expires_at);
