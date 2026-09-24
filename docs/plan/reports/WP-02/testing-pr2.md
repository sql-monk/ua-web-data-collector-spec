# WP-02 PR2 — testing report

## Executed evidence

On Windows with local Docker/PostgreSQL fixtures:

```text
uv run ruff check .                         All checks passed
uv run ruff format --check .                439 files already formatted
uv run mypy src                             Success: 118 source files
uv run pre-commit run --all-files           all hooks passed
uv run pytest -m "not live" -q              3893 passed, 25 skipped, 8 warnings in 1062.85s
```

The 25 local skips are GUI/stack enforcement cases that require an already running Compose
stack, the integration skip guards without `COLLECTOR_TEST_REQUIRE_DOCKER=1`, and one documented
Windows socket case. GitHub jobs set the required flags and must execute these suites.

After the final fail-closed robots correction:

```text
uv run pytest tests/integration/fetch/test_handler.py \
  tests/unit/fetch/test_robots.py tests/unit/fetch/test_metrics.py -q
14 passed in 5.61s
```

## Acceptance coverage

- Crash/fencing/dedup/corruption/sweeper: `test_claimed_upload.py`, `test_storage_minio.py`.
- Conditional GET, raw lineage, parse enqueue, pause, 304, 403/browser and 429:
  `test_handler.py`.
- Robots TTL, raw snapshot without parse job, `respect` deny and unavailable-robots fail closed:
  `test_handler.py`, `test_robots.py`.
- Aggregate rate limit across replicas and origin block: `test_permits_pg.py`.
- Finite four-attempt retry/dead letter: existing runtime contract
  `tests/integration/scaling/test_handler_plumbing.py` plus this handler's exact retry schedule.
- LOGIN-role grants/denials: `tests/integration/postgres/test_role_logins.py`; handler uses only
  the same repositories and `collector_fetcher` grants.
- Missing credentials: lazy store readiness tests and CI runtime secret wiring.

Mutation evidence for SSRF/body/permit paths remains in `testing-pr1.md`; PR2 adds database and
object-store fault injection at the transaction boundaries.

## Final GitHub evidence

Run `36059903510` on head `2e08b13`: 8/8 passed — Python 3m31s, PostgreSQL 2m13s,
PostgreSQL+MinIO 2m32s, MongoDB 1m13s, web 1m02s, Docker clean-host 5m29s, pre-commit 15s,
gitleaks 5s. PR #17 merged only after this run.
