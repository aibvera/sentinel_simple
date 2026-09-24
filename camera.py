"""Conexión resiliente a la cámara RTSP: reconexión automática con backoff exponencial."""

import logging
import os
import re
import time

import cv2
import numpy as np

from config import Config

log = logging.getLogger("camera")

DEFAULT_FPS = 15.0


def mask_url(url: str) -> str:
    """Oculta usuario y contraseña de una URL para poder mostrarla en logs."""
    return re.sub(r"//[^/@]+@", "//***:***@", url)


class Camera:
    """Fuente de frames RTSP que se reconecta sola cuando la señal se pierde."""

    def __init__(self, config: Config):
        """Prepara la cámara (la conexión real ocurre en la primera lectura)."""
        self._url = config.rtsp_url
        self._params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(config.open_timeout_seconds * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(config.read_timeout_seconds * 1000),
        ]
        self._max_failures = max(1, config.max_read_failures)
        self._max_delay = max(1.0, config.max_reconnect_delay_seconds)
        self._cap: cv2.VideoCapture | None = None
        self._failures = 0
        self._delay = 1.0
        self._next_attempt = 0.0
        self.fps = DEFAULT_FPS
        # El backend FFmpeg de OpenCV lee esta variable al abrir cada conexión.
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{config.rtsp_transport}"

    def read(self) -> np.ndarray | None:
        """Devuelve el siguiente frame, o None si no hay señal (reconecta en segundo plano)."""
        if self._cap is None and not self._connect():
            return None

        try:
            ok, frame = self._cap.read()
        except cv2.error as exc:
            log.warning("Error de OpenCV al leer frame: %s", exc)
            ok, frame = False, None

        if ok and frame is not None and frame.size > 0:
            self._failures = 0
            return frame

        self._failures += 1
        log.debug("Lectura fallida (%d/%d)", self._failures, self._max_failures)
        if self._failures >= self._max_failures:
            log.warning("Se perdió la señal de la cámara; se reconectará")
            self._disconnect()
            self._schedule_reconnect()
        return None

    def release(self) -> None:
        """Cierra la conexión con la cámara."""
        self._disconnect()

    def _connect(self) -> bool:
        """Intenta abrir la conexión si ya pasó el tiempo de espera del backoff."""
        remaining = self._next_attempt - time.monotonic()
        if remaining > 0:
            # Espera corta para no saturar la CPU, sin bloquear el loop principal.
            time.sleep(min(remaining, 0.5))
            return False

        log.info("Conectando a %s ...", mask_url(self._url))
        try:
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, self._params)
        except cv2.error as exc:
            log.error("Error de OpenCV al conectar: %s", exc)
            cap = None

        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            log.error("No se pudo conectar; reintento en %.0f s", self._delay)
            self._schedule_reconnect()
            return False

        self._cap = cap
        self._failures = 0
        self._delay = 1.0
        self.fps = self._sane_fps(cap.get(cv2.CAP_PROP_FPS))
        log.info("Cámara conectada (%.2f FPS)", self.fps)
        return True

    def _disconnect(self) -> None:
        """Libera el objeto de captura si existe."""
        if self._cap is not None:
            try:
                self._cap.release()
            except cv2.error:
                pass
            self._cap = None

    def _schedule_reconnect(self) -> None:
        """Programa el próximo intento de conexión duplicando la espera (hasta el máximo)."""
        self._next_attempt = time.monotonic() + self._delay
        self._delay = min(self._delay * 2, self._max_delay)

    @staticmethod
    def _sane_fps(fps: float) -> float:
        """Valida el FPS reportado por la cámara; usa un valor por defecto si es absurdo."""
        if 1.0 <= fps <= 60.0:
            return fps
        log.warning("FPS reportado inválido (%s); se usará %.0f", fps, DEFAULT_FPS)
        return DEFAULT_FPS
