"""Типізовані async-операції над `AsyncSession` (§7.2 queue, §7.6 limiter/pools, §13 audit).

Спільний контракт усіх функцій:

- перший аргумент — `AsyncSession`; функція не викликає `commit()`/`rollback()` — межа
  транзакції належить викликачу (`async with session.begin(): ...`), і кожна функція у
  docstring каже, чи має вона бути єдиною/короткою транзакцією (claim/acquire тримають row
  locks до commit);
- час — параметр `now: datetime | None` (див. `clock.py`), не `now()` бази;
- помилки — `collector.persistence.postgres.errors.*`, не `sqlalchemy.exc.*`;
- інші WP не пишуть SQL до цих таблиць самі.
"""
