"""SIGTERM/SIGINT → graceful drain (§7.5: «SIGTERM запускає drain, SIGKILL є fault case»).

Контейнер отримує SIGTERM від `docker compose stop`/`down` і від orchestrator-а при scale-down.
Процес має **не** вмирати одразу: runtime переходить у `draining`, дотягує активні tasks у межах
`stop_grace_period` і завершується кодом 0. SIGKILL (по вичерпанні `stop_grace_period`) не
перехоплюється — lease відновлює `recover_expired_leases` іншого instance.

`loop.add_signal_handler` доступний лише на POSIX; на Windows (локальні тести) ставиться
звичайний `signal.signal` із `call_soon_threadsafe`. Попередні handlers відновлюються
`restore()`, щоб вбудований запуск runtime (pytest, `collector` як бібліотека) не залишав
глобальних побічних ефектів.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Callable
from contextlib import suppress
from types import FrameType

STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGINT)


class StopSignalHandlers:
    """Встановлені handlers сигналів зупинки; `restore()` повертає попередні."""

    def __init__(
        self,
        *,
        loop_signals: tuple[signal.Signals, ...] = (),
        previous: dict[signal.Signals, object] | None = None,
    ) -> None:
        self.loop_signals = loop_signals
        self.previous = dict(previous or {})

    def restore(self) -> None:
        """Повернути попередні handlers (ідемпотентно)."""
        loop = asyncio.get_running_loop()
        for sig in self.loop_signals:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        self.loop_signals = ()
        for sig, handler in self.previous.items():
            with suppress(ValueError, OSError):
                # `signal.getsignal` повертає `Handlers | Callable | None`; звузити тип без
                # приватного аліаса `signal._HANDLER` не можна (той самий прийом у cli.py).
                signal.signal(sig, handler)  # type: ignore[arg-type]
        self.previous.clear()


def install_stop_signal_handlers(
    on_stop: Callable[[signal.Signals], None],
    *,
    signals: tuple[signal.Signals, ...] = STOP_SIGNALS,
) -> StopSignalHandlers:
    """Викликати `on_stop(signal)` у event loop при SIGTERM/SIGINT.

    Handlers ставляться лише з main thread (обмеження `signal.signal`); з іншого потоку
    повертається порожній `StopSignalHandlers` — зупинку в такому разі робить викликач через
    свій stop-event (так працюють тести й вбудований запуск).
    """
    if threading.current_thread() is not threading.main_thread():
        return StopSignalHandlers()
    loop = asyncio.get_running_loop()
    loop_signals: list[signal.Signals] = []
    previous: dict[signal.Signals, object] = {}
    for sig in signals:
        try:
            loop.add_signal_handler(sig, on_stop, sig)
        except (NotImplementedError, RuntimeError):
            previous[sig] = signal.getsignal(sig)

            def _handler(
                signum: int, _frame: FrameType | None, _loop: asyncio.AbstractEventLoop = loop
            ) -> None:
                _loop.call_soon_threadsafe(on_stop, signal.Signals(signum))

            signal.signal(sig, _handler)
        else:
            loop_signals.append(sig)
    return StopSignalHandlers(loop_signals=tuple(loop_signals), previous=previous)


__all__ = ["STOP_SIGNALS", "StopSignalHandlers", "install_stop_signal_handlers"]
