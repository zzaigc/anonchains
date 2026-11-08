# Pool data directory

This directory holds the two runtime pools. Nothing here should ever be
committed; see the repository `.gitignore`.

| Directory | File | Meaning |
| --- | --- | --- |
| `candidate/` | `candidate.db` | Raw, deduplicated nodes parsed from sources. Not yet proven usable. |
| `verified/` | `verified.db` | Nodes that passed connectivity, exit-IP and anonymity checks. |
| `.` | `.vault` | Encrypted Vault holding the data key wrapper. Never commit or share it. |

Both `.db` files are SQLite databases managed by `anonchains/pools/store.py`.
Columns are documented in `docs/02-data-model-and-pools.md`. Sensitive columns
(`credential`, `raw`) are sealed with the Vault before being written; if the
Vault is locked or disabled those columns are left `NULL` rather than being
written in plaintext.

## Backup

Back up `candidate.db`, `verified.db` **and** `.vault` together, plus your
passphrase stored separately. A backup without the Vault file (or without the
passphrase) cannot restore sealed credentials.

## Reset

Stop anonchains first, then delete the two `.db` files to start with empty
pools. Deleting `.vault` destroys all sealed credentials permanently.
