"""SIGTERM-контракт runtime і CLI-команди `worker`/`scheduler` без БД (WP-01D PR1).

Сигнали перевіряються без реального `raise_signal`: тест викликає встановлений handler
напряму — результат детермінований і однаковий на Linux (CI) і Windows (розробка), де
`loop.add_signal_handler` недоступний і працює fallback через `signal.signal`.
"""

from __future__ import annotations

import asyncio
import signal
import threading

import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.workers.signals import STOP_SIGNALS, install_stop_signal_handlers

runner = CliRunner()


def _raise_not_implemented(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError


async def test_fallback_handler_schedules_stop_and_restores_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    # Windows-шлях (`add_signal_handler` недоступний) примусово — щоб тест перевіряв саме його.
    monkeypatch.setattr(loop, "add_signal_handler", _raise_not_implemented)
    before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    received: list[signal.Signals] = []

    installed = install_stop_signal_handlers(received.append)
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)
    assert handler not in before.values()

    handler(int(signal.SIGTERM), None)
    await asyncio.sleep(0)
    assert received == [signal.SIGTERM]

    installed.restore()
    assert {sig: signal.getsignal(sig) for sig in STOP_SIGNALS} == before


async def test_handlers_are_not_installed_from_a_worker_thread() -> None:
    """Вбудований запуск (тести, бібліотека) не має чіпати глобальні handlers процесу."""
    before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    result: list[bool] = []

    def run() -> None:
        loop = asyncio.new_event_loop()
        try:

            async def install() -> None:
                installed = install_stop_signal_handlers(lambda _sig: None)
                result.append(bool(installed.loop_signals or installed.previous))

            loop.run_until_complete(install())
        finally:
            loop.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert result == [False]
    assert {sig: signal.getsignal(sig) for sig in STOP_SIGNALS} == before


def test_worker_without_dsn_exits_1_with_config_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)
    result = runner.invoke(app, ["worker", "fetch"])
    assert result.exit_code == 1, result.output
    assert "COLLECTOR_POSTGRES_DSN" in result.output
    assert "not implemented" not in result.output


def test_worker_rejects_bad_runtime_config_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("COLLECTOR_WORKER_LEASE_SECONDS", "10")
    monkeypatch.setenv("COLLECTOR_WORKER_HEARTBEAT_SECONDS", "9")
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)
    result = runner.invoke(app, ["worker", "fetch"])
    assert result.exit_code == 1, result.output
    assert "worker config" in result.output


def test_scheduler_without_dsn_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)
    result = runner.invoke(app, ["scheduler"])
    assert result.exit_code == 1, result.output
    assert "COLLECTOR_POSTGRES_DSN" in result.output


def test_scheduler_rejects_bad_config_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("COLLECTOR_SCHEDULER_TICK_SECONDS", "0")
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)
    result = runner.invoke(app, ["scheduler"])
    assert result.exit_code == 1, result.output
    assert "scheduler config" in result.output
