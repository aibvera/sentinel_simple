"""Organización de videos en carpetas por día y limpieza automática del disco."""

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from config import Config

log = logging.getLogger("storage")

VIDEO_PATTERN = "*/*.mp4"
# Un archivo modificado hace menos de esto se considera "en grabación" y no se borra.
ACTIVE_FILE_SECONDS = 120


class Storage:
    """Gestiona la carpeta de videos: rutas por día, retención y espacio libre."""

    def __init__(self, config: Config):
        """Guarda la configuración de almacenamiento (la limpieza corre en maintain())."""
        self._root = config.save_path
        self._config = config
        self._retention_seconds = config.elimination_days * 86400
        self._min_free_bytes = config.min_free_mb * 1024 * 1024
        self._interval = max(1.0, config.cleanup_interval_minutes * 60)
        self._next_cleanup = 0.0

    def new_clip_path(self, start: datetime) -> Path:
        """Crea (si hace falta) la carpeta del día y devuelve una ruta libre para un nuevo clip."""
        day_dir = self._root / start.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        base = start.strftime("%H-%M-%S")
        path = day_dir / f"{base}.mp4"
        suffix = 1
        while path.exists():
            path = day_dir / f"{base}_{suffix}.mp4"
            suffix += 1
        return path

    def maintain(self) -> None:
        """Ejecuta la limpieza periódicamente (cada CLEANUP_INTERVAL_MINUTES)."""
        now = time.monotonic()
        if now < self._next_cleanup:
            return
        self._next_cleanup = now + self._interval
        try:
            self._delete_expired()
            self._ensure_free_space()
            self._remove_empty_days()
        except OSError as exc:
            log.error("Error durante la limpieza de %s: %s", self._root, exc)

    def _videos(self) -> list[tuple[float, Path]]:
        """Lista los videos existentes como (fecha de modificación, ruta), del más viejo al más nuevo."""
        if not self._root.is_dir():
            return []
        videos = []
        for path in self._root.glob(VIDEO_PATTERN):
            try:
                videos.append((path.stat().st_mtime, path))
            except OSError:
                continue  # El archivo desapareció mientras se listaba.
        return sorted(videos)

    def _delete_expired(self) -> None:
        """Borra los videos con más de ELIMINATION_DAYS días de antigüedad."""
        if self._retention_seconds <= 0:
            return
        cutoff = time.time() - self._retention_seconds
        for mtime, path in self._videos():
            if mtime >= cutoff:
                break
            self._delete(path, "antigüedad mayor a %.0f días" % self._config.elimination_days)

    def _ensure_free_space(self) -> None:
        """Si queda menos de MIN_FREE_MB libre, borra los videos más viejos hasta recuperarlo."""
        if self._min_free_bytes <= 0 or not self._root.is_dir():
            return
        if shutil.disk_usage(self._root).free >= self._min_free_bytes:
            return
        log.warning("Poco espacio libre en disco; se borrarán los videos más antiguos")
        active_since = time.time() - ACTIVE_FILE_SECONDS
        for mtime, path in self._videos():
            if shutil.disk_usage(self._root).free >= self._min_free_bytes:
                return
            if mtime < active_since:
                self._delete(path, "espacio libre bajo")
        log.error("No se pudo liberar suficiente espacio (mínimo %d MB)", self._config.min_free_mb)

    def _remove_empty_days(self) -> None:
        """Elimina las carpetas de días que quedaron vacías."""
        if not self._root.is_dir():
            return
        for day_dir in self._root.iterdir():
            if day_dir.is_dir() and not any(day_dir.iterdir()):
                try:
                    day_dir.rmdir()
                except OSError:
                    pass

    @staticmethod
    def _delete(path: Path, reason: str) -> None:
        """Borra un video registrando el motivo; los errores no detienen la limpieza."""
        try:
            path.unlink()
            log.info("Video borrado (%s): %s", reason, path)
        except OSError as exc:
            log.warning("No se pudo borrar %s: %s", path, exc)
