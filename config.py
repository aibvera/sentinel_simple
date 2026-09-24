"""Carga y valida la configuración del sistema desde variables de entorno / archivo .env."""

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    # Cámara
    rtsp_url: str
    rtsp_transport: str
    open_timeout_seconds: float
    read_timeout_seconds: float
    max_read_failures: int
    max_reconnect_delay_seconds: float
    # Detección de movimiento
    disimil_th: float
    pixel_diff_th: int
    motion_width: int
    # Grabación
    save_path: Path
    pre_record_seconds: float
    post_record_seconds: float
    max_clip_seconds: float
    video_crf: int
    video_preset: str
    # Almacenamiento
    elimination_days: float
    min_free_mb: int
    cleanup_interval_minutes: float
    # Sistema
    timezone: tzinfo | None
    watchdog_seconds: float
    log_level: str

    def now(self) -> datetime:
        """Devuelve la fecha/hora actual en la zona horaria configurada."""
        return datetime.now(self.timezone)


def _get(name: str, default: str | None = None) -> str:
    """Lee una variable de entorno; termina el programa si es obligatoria y no existe."""
    value = os.environ.get(name, default)
    if value is None or value.strip() == "":
        if default is None:
            sys.exit(f"Falta la variable de entorno obligatoria: {name}")
        return default
    return value.strip()


def _get_number(name: str, default: str, cast: type) -> float | int:
    """Lee una variable de entorno numérica y valida que no sea negativa."""
    raw = _get(name, default)
    try:
        value = cast(raw)
    except ValueError:
        sys.exit(f"La variable {name} debe ser numérica (valor actual: {raw!r})")
    if value < 0:
        sys.exit(f"La variable {name} no puede ser negativa (valor actual: {raw!r})")
    return value


def _get_timezone(name: str) -> tzinfo | None:
    """Devuelve la zona horaria configurada, o None para usar la hora local del sistema."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return ZoneInfo(raw)
    except Exception:
        sys.exit(f"Zona horaria inválida en {name}: {raw!r} (ejemplo: America/Bogota)")


def load_config() -> Config:
    """Carga el archivo .env (si existe) y construye la configuración validada."""
    load_dotenv(PROJECT_DIR / ".env")

    save_path = Path(_get("SAVE_PATH", "recordings"))
    if not save_path.is_absolute():
        save_path = PROJECT_DIR / save_path

    return Config(
        rtsp_url=_get("RTSP_URL"),
        rtsp_transport=_get("RTSP_TRANSPORT", "tcp"),
        open_timeout_seconds=_get_number("OPEN_TIMEOUT_SECONDS", "10", float),
        read_timeout_seconds=_get_number("READ_TIMEOUT_SECONDS", "10", float),
        max_read_failures=_get_number("MAX_READ_FAILURES", "5", int),
        max_reconnect_delay_seconds=_get_number("MAX_RECONNECT_DELAY_SECONDS", "30", float),
        disimil_th=_get_number("DISIMIL_TH", "0.5", float),
        pixel_diff_th=_get_number("PIXEL_DIFF_TH", "25", int),
        motion_width=_get_number("MOTION_WIDTH", "320", int),
        save_path=save_path,
        pre_record_seconds=_get_number("PRE_RECORD_SECONDS", "2", float),
        post_record_seconds=_get_number("POST_RECORD_SECONDS", "5", float),
        max_clip_seconds=_get_number("MAX_CLIP_SECONDS", "300", float),
        video_crf=_get_number("VIDEO_CRF", "26", int),
        video_preset=_get("VIDEO_PRESET", "veryfast"),
        elimination_days=_get_number("ELIMINATION_DAYS", "7", float),
        min_free_mb=_get_number("MIN_FREE_MB", "1024", int),
        cleanup_interval_minutes=_get_number("CLEANUP_INTERVAL_MINUTES", "10", float),
        timezone=_get_timezone("TIMEZONE"),
        watchdog_seconds=_get_number("WATCHDOG_SECONDS", "60", float),
        log_level=_get("LOG_LEVEL", "INFO").upper(),
    )


def setup_logging(config: Config) -> None:
    """Configura el logging a consola usando la zona horaria del sistema de vigilancia."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    formatter.converter = lambda ts: datetime.fromtimestamp(ts, config.timezone).timetuple()
    handler.setFormatter(formatter)
    logging.basicConfig(level=config.log_level, handlers=[handler], force=True)
