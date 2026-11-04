# ADR 001 — Vault uses a single atomic PBKDF2 file

Status: accepted (rc.5)

## Context
The agent needs a local owner credential that survives restarts without an OS
keychain dependency and works on portable installs.

## Decision
Store `owner.lock` as a single `0600` file containing a versioned record with a
random salt, PBKDF2-SHA256 (600,000 iterations) verifier and a canary. Rotation
is explicit (`--set-password`). Tokens are session-scoped and memory-only.

## Consequences
- No plaintext password is stored.
- Offline guessing is bounded by PBKDF2 cost.
- Losing the file requires explicit `--force` recovery.
- At-rest document encryption is out of scope; full-disk encryption is advised.
