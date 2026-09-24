"""Detección de movimiento por disimilitud entre frames consecutivos."""

import logging

import cv2
import numpy as np

from config import Config

log = logging.getLogger("motion")


class MotionDetector:
    """Compara cada frame con el anterior y decide si hubo movimiento."""

    def __init__(self, config: Config):
        """Guarda los umbrales de detección."""
        self._threshold = config.disimil_th
        self._pixel_diff_th = config.pixel_diff_th
        self._width = max(32, config.motion_width)
        self._previous: np.ndarray | None = None
        self.last_score = 0.0

    def detect(self, frame: np.ndarray | None) -> bool:
        """Devuelve True si el % de píxeles distintos al frame anterior supera DISIMIL_TH.

        Un frame None (sin señal) reinicia la referencia, para no comparar contra
        una imagen vieja cuando la cámara vuelve a conectarse.
        """
        if frame is None:
            self._previous = None
            return False

        current = self._prepare(frame)
        previous, self._previous = self._previous, current
        if previous is None or previous.shape != current.shape:
            return False

        diff = cv2.absdiff(previous, current)
        _, changed = cv2.threshold(diff, self._pixel_diff_th, 255, cv2.THRESH_BINARY)
        self.last_score = 100.0 * cv2.countNonZero(changed) / changed.size

        if self.last_score > self._threshold:
            log.debug("Movimiento: disimilitud %.2f%% > %.2f%%", self.last_score, self._threshold)
            return True
        return False

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        """Reduce, pasa a escala de grises y suaviza el frame para ignorar ruido fino."""
        height, width = frame.shape[:2]
        small_height = max(1, round(height * self._width / width))
        small = cv2.resize(frame, (self._width, small_height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)
