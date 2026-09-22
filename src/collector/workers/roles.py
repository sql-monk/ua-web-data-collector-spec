"""Ролі worker pools за §7.6 ТЗ. Реалізація ролей — WP-01D; тут лише перелік назв."""

from __future__ import annotations

from enum import StrEnum


class WorkerRole(StrEnum):
    """Назви ролей збігаються з `worker_pools.role` і CLI `collector worker <role>`."""

    DISCOVERY = "discovery"
    FETCH = "fetch"
    BROWSER = "browser"
    PARSE = "parse"
    PROJECTOR = "projector"
    TRANSLATION = "translation"
    EXPORT = "export"
    MAINTENANCE = "maintenance"
