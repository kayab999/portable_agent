# ADR 008 — Vault cryptography review

## Status

Accepted as a documented review item. External cryptographic review remains
recommended before a stable release.

## Context

Portable Agent is stdlib-only and cannot depend on a third-party authenticated
cipher implementation. The vault therefore uses PBKDF2-HMAC-SHA256 for key
derivation and an HMAC-SHA256 CTR stream with Encrypt-then-MAC for canary
encryption.

## Decision

Keep the custom construction under the following constraints:

- no new runtime cryptographic dependencies without an ADR;
- Encrypt-then-MAC remains mandatory;
- legacy v0 ciphertext remains rejected by default;
- PBKDF2 iterations remain bounded by `MIN_ENC_ITERATIONS` and
  `MAX_ENC_ITERATIONS`;
- all vault operations remain local-only.

## Consequences

- The construction is reviewed internally but is not FIPS-validated.
- External cryptographic review is still recommended.
- Any change to the cipher or KDF requires a new ADR and migration plan.