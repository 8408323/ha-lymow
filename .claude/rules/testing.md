---
alwaysApply: true
---

# Testing

- Verify behavior, not implementation. Don't assert mock call counts when output values would do.
- Run the specific test file after changes, not the full suite. Faster feedback, fewer tokens: `uv run pytest tests/test_<module>.py -k <pattern>`. The full suite enforces 100% coverage (`--cov-fail-under=100`), so a new branch needs a covering test before CI passes.
- Flaky test? Fix it or delete it. Never retry to make it pass.
- Prefer real implementations. Mock only at system boundaries (network, filesystem, clock, randomness, the AWS/MQTT cloud). Don't mock the protobuf codec — build real payloads with the test helpers and assert the decoded output.
- Keep each test focused on one behavior. Multiple `assert`s in a single test are fine when they verify aspects of the same observed output (the existing pytest suite does this). Test names should still describe one behavior. Arrange-Act-Assert.
- Never `assert True` or check a mock was called without verifying arguments.
