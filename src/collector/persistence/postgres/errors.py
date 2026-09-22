"""Типізовані помилки репозиторіїв (викликачі ловлять їх замість `sqlalchemy.exc.*`)."""

from __future__ import annotations


class PersistenceError(Exception):
    """База всіх помилок persistence.postgres."""


class NotFoundError(PersistenceError):
    """Рядок за ключем відсутній."""


class StaleRevisionError(PersistenceError):
    """Optimistic revision не збігся: ресурс змінено кимось іншим (§7.6 stale GUI action)."""


class LeaseNotOwnedError(PersistenceError):
    """Lease належить іншому owner, прострочений або job уже не в статусі `leased`."""


class InvalidTransitionError(PersistenceError):
    """Недозволений перехід стану (scale command, crawl run, worker instance)."""


class ConflictError(PersistenceError):
    """Порушення бізнес-унікальності: другий повний обхід джерела, дубль ключа тощо."""
