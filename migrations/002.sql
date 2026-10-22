-- Root names are permanent operator-managed aliases, never new identities.
CREATE TABLE root_aliases (
  name TEXT PRIMARY KEY,
  ref TEXT NOT NULL UNIQUE REFERENCES packages(ref),
  created_at INTEGER NOT NULL
);
