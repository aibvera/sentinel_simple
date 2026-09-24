"""Ciclo de vida del proceso: apagado ordenado por señales y watchdog anti-bloqueos."""

import logging
import os
import signal
import threading
import time

log = logging.getLogger("lifecycle")


def install_signal_handlers() -> threading.Event:
    """Devuelve un evento que se activa con Ctrl+C o `docker stop` (SIGINT/SIGTERM)."""
    stop_event = threading.Event()

    def _handle(signum, _frame):
        log.info("Señal %s recibida; apagando de forma ordenada...", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    return stop_event


class Watchdog:
    """Termina el proceso si el loop principal deja de latir por demasiado tiempo.

    Cubre bloqueos que ningún timeout atrapa (drivers, red, disco). En Docker, la política
    `--restart` vuelve a levantar el contenedor automáticamente.
    """

    def __init__(self, timeout_seconds: float):
        """Arranca el hilo vigilante (timeout 0 lo desactiva)."""
        self._timeout = timeout_seconds
        self._last_beat = time.monotonic()
        self._stopped = threading.Event()
        if self._timeout > 0:
            threading.Thread(target=self._watch, daemon=True).start()

    def beat(self) -> None:
        """Marca que el loop principal sigue vivo."""
        self._last_beat = time.monotonic()

    def stop(self) -> None:
        """Detiene la vigilancia (al apagar el sistema)."""
        self._stopped.set()

    def _watch(self) -> None:
        """Revisa periódicamente el último latido y aborta el proceso si está congelado."""
        while not self._stopped.wait(1.0):
            stalled = time.monotonic() - self._last_beat
            if stalled > self._timeout:
                log.critical("Loop principal bloqueado %.0f s; se reinicia el proceso", stalled)
                logging.shutdown()
                os._exit(1)
