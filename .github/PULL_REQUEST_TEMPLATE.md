<!-- Thanks for contributing! Keep PRs focused — one logical change per PR. -->

## Summary

<!-- What does this change and why? -->

## Related issue

<!-- e.g. Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / internal
- [ ] Docs / CI / chore

## Checklist

- [ ] Branch name uses a type prefix (`feat/`, `fix/`, `chore/`, `tests/`, `docs/`, ...).
- [ ] `uv run ruff check` and `uv run ruff format --check` pass.
- [ ] Tests pass with 100% coverage: `uv run pytest tests/ --cov=custom_components/lymow --cov-fail-under=100` (plain `pytest` won't enforce coverage; CI does).
- [ ] hassfest / HACS validation pass (or N/A).
- [ ] No sensitive data committed (tokens, PIN, GPS, `thingName`, email, capture artifacts).
- [ ] Docs/README updated if behavior changed.
