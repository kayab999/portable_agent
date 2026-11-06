# Contributing

## Local setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt   # dev tools only; runtime is stdlib-only
python3 launcher.py --setup           # optional: model + llama-server
python3 launcher.py --doctor
```

The runtime itself is **stdlib-only**. Development tools may be installed in the
virtualenv: `coverage`, `ruff`, `mypy`, `hypothesis`, `pip-audit`, `bandit`,
`playwright`, `mutmut`. See `docs/TESTING.md` for the full tooling guide,
including browser and mutation-testing options.

## Quality gates

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_coverage.py
ruff check .
scripts/ci.sh
```

Optional, when installed:

```bash
mypy <module>          # strict typing on critical modules
pip-audit -r requirements-dev.txt
bandit -r . -x .venv   # security lint
python scripts/secret_scan.py --history
python scripts/generate_sbom.py
```

## Rules

- Runtime code must not import third-party packages.
- New capability surfaces default **off** and cannot widen another surface.
- External content (files, MCP, RAG, model output) is untrusted; never let it
  grant tools or permissions.
- Every state-changing endpoint needs auth and rate limiting.
- Add a regression test for every security or data-integrity fix.
- Keep AI-generated code reviewed: no unreviewed bulk refactors.
- Preserve the existing module naming and Spanish/English documentation style
  where it already exists.

## Commits and releases

Use scoped messages, for example:

```
rag: add schema v2 migrations and BM25 search
persistence: replace select/delete queue with leases
security: intersect subagent tools with persona allowlist
```

Tag releases (`v1.0.0-rc.5`) after `scripts/ci.sh` passes and `docs/RC_DOD.md` is
updated. Never commit `config.json`, `owner.lock`, `identity.json`, `data/`,
`models/` or workspace contents.

## Adding a dependency

Runtime dependencies are a design change, not a convenience. Open an ADR first.
Dev-only tools are fine but must stay out of the runtime path.
