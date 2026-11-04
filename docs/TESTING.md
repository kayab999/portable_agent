# Testing and quality tooling

The runtime is stdlib-only. Everything on this page is **development tooling**
and lives in a virtualenv, never in the runtime path.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
```

## Required gates

These run locally and in CI (`.github/workflows/ci.yml`):

```bash
# Full unit/integration suite (Linux first-class target)
python3 -m unittest discover -s tests -p 'test_*.py'

# Branch coverage + per-module floors
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_coverage.py

# Lint
ruff check .

# Everything: smoke, tests, adversarial, audit suite, E2E boot, lint
scripts/ci.sh

# Adversarial suite
python3 redteam/run_all.py
```

`tests/test_start_ps1.py` is skipped when `pwsh` is absent; the Windows CI job
runs it with PowerShell available. `tests/test_e2e_boot.py` needs a llama-server
binary and a tiny `.gguf` and skips cleanly otherwise.

## Import contracts

`pyproject.toml` declares two architecture invariants:

- storage (`local_memory`, `experience_store`, `maintenance`, `audit_worker`)
  must not import transports (`gateway`, `acp_server`, `launcher`);
- `acp_server` must not import `gateway` (shared code lives in `model_client`
  and `runtime`).

```bash
lint-imports
```

If import-linter is not installed, `scripts/ci.sh` reports the skip instead of
failing; the dedicated CI job installs it.

## Type checking

`pyproject.toml` carries a pragmatic mypy baseline (`check_untyped_defs`,
missing imports ignored, legacy/archived trees excluded). It is a ratchet, not
yet a strict-typing claim:

```bash
mypy .
```

When tightening a module, enable `disallow_untyped_defs` for that module in an
override and keep the rest of the baseline unchanged.

## Dependency and security audit

```bash
pip-audit
bandit -r . -x .venv,archive,tests
```

Runtime has no third-party dependencies, so `pip-audit` mainly covers dev tools.

## Property-based testing

Hypothesis is available for parsers and storage invariants. Good candidates:

- `local_memory._fts_query` never produces an executable FTS operator;
- `experience_store` idempotency keys are stable under payload key reordering;
- `context_builder` delimiters cannot be closed by external content.

Existing unit tests already cover the concrete cases; add properties next to
them rather than replacing them.

## Browser / accessibility checks (optional)

The static contract (external assets, no inline handlers, CSP without inline
script) is enforced by `tests/test_web_static.py` with no browser dependency.
For a real render pass:

```bash
playwright install chromium
# With the gateway running locally:
python3 -m playwright open http://127.0.0.1:8080
```

Recommended checks: console errors, OOBE flow, chat send/cancel, RAG citation
rendering, and an axe-core accessibility scan. These are **not** currently
automated in this environment (Playwright is not installed), so no browser or
WCAG conformance claim is made.

## Mutation testing (optional)

`mutmut` is listed for measuring suite strength on pure logic:

```bash
mutmut run --paths-to-mutate=local_memory.py,experience_store.py
mutmut results
```

Start with the FTS query builder, chunking, scoring and queue backoff. Surviving
mutants in those areas indicate missing assertions, not necessarily bugs. This
is an advisory tool and is not wired into CI.
