# Contributing

Thanks for your interest in improving **ha-lymow**. This is an unofficial,
reverse-engineered Home Assistant integration maintained in spare time, so please
keep changes focused and well-tested.

## Ground rules

- **Never commit sensitive data.** Auth tokens (JWTs), the robot PIN, GPS
  coordinates, the real device `thingName`, and account emails must not appear in
  source, tests, commits, issues, or PR descriptions. Capture artifacts under
  `tools/` are gitignored — keep it that way.
- One logical change per pull request.
- By contributing, you agree your work is licensed under the repository's
  [MIT License](LICENSE).

## Development setup

This project uses [uv](https://docs.astral.sh/uv/) and targets **Python 3.13**.

```bash
uv sync --dev
```

## Before you push

Run the same checks CI enforces:

```bash
# Lint + format
uv run ruff check custom_components/ tests/ scripts/ tools/
uv run ruff format --check custom_components/ tests/ scripts/ tools/

# Tests — 100% coverage is required
uv run pytest tests/ -v --tb=short \
  --cov=custom_components/lymow --cov-report=term-missing --cov-fail-under=100
```

`hassfest` and HACS validation also run in CI; you don't need to run them locally.

## Pull requests

- Branch from `main` using a type prefix: `feat/`, `fix/`, `chore/`, `tests/`,
  `docs/`, `refactor/`, `ci/`, etc.
- `main` is protected: PRs merge by **squash** only, all CI checks must pass, the
  branch must be up to date with `main`, and all review threads must be resolved.
- Fill out the PR template and link any related issue.

## Reporting bugs & security issues

- Bugs: open an issue using the bug-report template (redact sensitive data first).
- Security: use the private [security advisory flow](../../security/advisories/new),
  not a public issue. See [SECURITY.md](SECURITY.md).
