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


class StaleClaimError(PersistenceError):
    """Commit predicate upload claim не виконано (§10 п.5, R-38/R-41).

    Причини: `claim_generation` застаріла (хтось зробив reacquire), lease прострочений, owner
    інший або claim уже не `leased`. Producer має повторно взяти claim, ще раз перевірити
    HEAD/checksum об'єкта і лише тоді commit-ити — stale generation не створює DB reference.
    """


class InvalidValueError(PersistenceError):
    """Аргумент операції порушує інваріант ресурсу (`desired_concurrency < 1`, replicas поза
    `min/max` тощо).

    Перевірка робиться у репозиторії **до першого запису**, щоб викликач отримав типізовану
    помилку і незіпсовану транзакцію; CHECK-константи у схемі лишаються другим рубежем захисту
    даних (пряма зміна рядка в обхід репозиторію).
    """
