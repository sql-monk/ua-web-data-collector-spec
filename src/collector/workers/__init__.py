"""Worker runtime §7.6 (owner — WP-01D): ролі, pool/instance контракти, claim-loop, lease
heartbeat, drain.

Пакет навмисно **не** реекспортує `runtime`/`scheduler`: вони тягнуть SQLAlchemy, а
`collector.cli` імпортує `collector.workers.roles` на рівні модуля (`collector version` і
Docker HEALTHCHECK мають лишатися дешевими — тест-вартовий
`tests/unit/persistence/postgres/test_cli_db.py`). Імпортуйте потрібний модуль явно:

- `collector.workers.roles` — `WorkerRole`, default desired state pools (§7.6);
- `collector.workers.handlers` — `TaskHandler`/`Task`/`TaskResult` для доменних WP;
- `collector.workers.config` — env-конфігурація worker/scheduler;
- `collector.workers.runtime` — `WorkerRuntime` (claim/lease/heartbeat/drain);
- `collector.workers.scheduler` — singleton `SchedulerRuntime` з advisory lease.
"""
