"""Grabación de clips mp4 disparada por movimiento, con pre y post grabación."""

import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from config import Config
from storage import Storage

log = logging.getLogger("recorder")

# Si falla la apertura de un clip, se espera esto antes de reintentar.
RETRY_SECONDS = 5.0
# Un mp4 más chico que esto solo tiene cabecera (p. ej. ffmpeg murió antes del primer fragmento).
MIN_CLIP_BYTES = 1024


def find_ffmpeg() -> str | None:
    """Busca un ejecutable de ffmpeg con soporte H.264 (libx264): el de imageio-ffmpeg o el del sistema."""
    candidates = []
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    candidates.append(shutil.which("ffmpeg"))

    for exe in filter(None, candidates):
        try:
            result = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15)
            if "libx264" in result.stdout:
                return exe
        except (OSError, subprocess.SubprocessError):
            continue
    return None


class FfmpegWriter:
    """Escribe frames a un mp4 H.264 fragmentado usando un proceso ffmpeg.

    El mp4 fragmentado se puede reproducir aunque el proceso muera a mitad de grabación.
    """

    def __init__(self, exe: str, path: Path, width: int, height: int, fps: float, crf: int, preset: str):
        """Lanza ffmpeg leyendo frames BGR crudos desde stdin."""
        gop = max(1, round(fps * 2))  # Un keyframe (y un fragmento) cada ~2 s.
        command = [
            exe, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-framerate", f"{fps:.3f}", "-i", "-",
            "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-g", str(gop),
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-flush_packets", "1",
            "-f", "mp4", str(path),
        ]
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        # Se leen los errores de ffmpeg en un hilo para que su buffer nunca se llene y lo bloquee.
        threading.Thread(target=self._log_errors, daemon=True).start()

    def write(self, frame: np.ndarray) -> None:
        """Envía un frame a ffmpeg; lanza OSError si el proceso murió."""
        if self._process.poll() is not None:
            raise OSError(f"ffmpeg terminó inesperadamente (código {self._process.returncode})")
        self._process.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        """Cierra stdin para que ffmpeg termine el archivo, y espera su salida."""
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg no terminó a tiempo; se fuerza el cierre")
            self._process.kill()
            self._process.wait()

    def _log_errors(self) -> None:
        """Reenvía al log cada línea de error que imprima ffmpeg."""
        for line in self._process.stderr:
            text = line.decode(errors="replace").strip()
            if text:
                log.warning("ffmpeg: %s", text)


class OpenCVWriter:
    """Escritor de respaldo (mp4v) usando OpenCV, por si ffmpeg no está disponible."""

    def __init__(self, path: Path, width: int, height: int, fps: float):
        """Abre el archivo de video con OpenCV."""
        self._writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (width, height))
        if not self._writer.isOpened():
            raise OSError(f"OpenCV no pudo crear el archivo {path}")

    def write(self, frame: np.ndarray) -> None:
        """Agrega un frame al video."""
        self._writer.write(frame)

    def close(self) -> None:
        """Finaliza el archivo de video."""
        self._writer.release()


class Recorder:
    """Máquina de estados que decide cuándo abrir, escribir, rotar y cerrar clips."""

    def __init__(self, config: Config, storage: Storage):
        """Prepara el buffer de pre-grabación y elige el backend de escritura."""
        self._config = config
        self._storage = storage
        self._pre_buffer: deque[tuple[float, np.ndarray]] = deque()
        self._writer: FfmpegWriter | OpenCVWriter | None = None
        self._path: Path | None = None
        self._shape: tuple[int, ...] | None = None
        self._clip_started = 0.0
        self._clip_day: date | None = None
        self._frames_written = 0
        self._last_motion = float("-inf")
        self._retry_at = 0.0

        self._ffmpeg = find_ffmpeg()
        if self._ffmpeg:
            log.info("Grabación con ffmpeg (H.264): %s", self._ffmpeg)
        else:
            log.warning("ffmpeg con libx264 no disponible; se usará OpenCV (mp4v)")

    def update(self, frame: np.ndarray | None, motion: bool, fps: float) -> None:
        """Procesa un ciclo: guarda el frame en el buffer o el clip, y abre/cierra clips según el movimiento."""
        now = time.monotonic()
        if motion:
            self._last_motion = now

        if self._writer is None:
            if frame is not None:
                self._buffer(frame, now)
                if motion and now >= self._retry_at:
                    self._start(frame, fps)
            return

        if now - self._last_motion > self._config.post_record_seconds:
            self._stop("fin del movimiento")
            return

        if frame is None:
            return  # Sin señal: se espera a que vuelva o a que venza la post-grabación.

        if self._needs_rotation(frame):
            self._stop("rotación de clip")
            self._start(frame, fps)
            return

        self._write(frame)

    def close(self) -> None:
        """Cierra el clip en curso (se usa al apagar el sistema)."""
        if self._writer is not None:
            self._stop("apagado del sistema")

    def _buffer(self, frame: np.ndarray, now: float) -> None:
        """Guarda el frame en el buffer de pre-grabación, descartando los más viejos que PRE_RECORD_SECONDS."""
        if self._config.pre_record_seconds <= 0:
            self._pre_buffer.clear()
            self._pre_buffer.append((now, frame))
            return
        if self._pre_buffer and self._pre_buffer[-1][1].shape != frame.shape:
            self._pre_buffer.clear()  # Cambió la resolución de la cámara.
        self._pre_buffer.append((now, frame))
        while now - self._pre_buffer[0][0] > self._config.pre_record_seconds:
            self._pre_buffer.popleft()

    def _start(self, frame: np.ndarray, fps: float) -> None:
        """Abre un clip nuevo y vuelca en él el buffer de pre-grabación."""
        height, width = frame.shape[:2]
        start = self._config.now()
        try:
            path = self._storage.new_clip_path(start)
            self._writer = self._open_writer(path, width, height, fps)
        except (OSError, ValueError) as exc:
            log.error("No se pudo iniciar la grabación: %s (reintento en %.0f s)", exc, RETRY_SECONDS)
            self._writer = None
            self._retry_at = time.monotonic() + RETRY_SECONDS
            return

        self._path = path
        self._shape = frame.shape
        self._clip_started = time.monotonic()
        self._clip_day = start.date()
        self._frames_written = 0
        log.info("Grabando: %s", path)

        buffered = [f for _, f in self._pre_buffer] or [frame]
        self._pre_buffer.clear()
        for buffered_frame in buffered:
            if self._writer is None:
                break
            self._write(buffered_frame)

    def _open_writer(self, path: Path, width: int, height: int, fps: float) -> FfmpegWriter | OpenCVWriter:
        """Crea el escritor de video con el mejor backend disponible."""
        if self._ffmpeg:
            return FfmpegWriter(
                self._ffmpeg, path, width, height, fps, self._config.video_crf, self._config.video_preset,
            )
        return OpenCVWriter(path, width, height, fps)

    def _write(self, frame: np.ndarray) -> None:
        """Escribe un frame; si falla el disco o ffmpeg, cierra el clip y reintenta más tarde."""
        try:
            self._writer.write(frame)
            self._frames_written += 1
        except (OSError, ValueError, cv2.error) as exc:
            log.error("Error escribiendo %s: %s", self._path, exc)
            self._stop("error de escritura")
            self._retry_at = time.monotonic() + RETRY_SECONDS

    def _needs_rotation(self, frame: np.ndarray) -> bool:
        """Indica si hay que partir el clip: duración máxima, cambio de día o de resolución."""
        if frame.shape != self._shape:
            return True
        if self._config.max_clip_seconds > 0 and time.monotonic() - self._clip_started >= self._config.max_clip_seconds:
            return True
        return self._config.now().date() != self._clip_day

    def _stop(self, reason: str) -> None:
        """Cierra el clip en curso sin propagar errores."""
        writer, self._writer = self._writer, None
        try:
            writer.close()
        except (OSError, ValueError, cv2.error) as exc:
            log.error("Error cerrando %s: %s", self._path, exc)
        if self._frames_written == 0 or self._file_size() < MIN_CLIP_BYTES:
            try:
                self._path.unlink(missing_ok=True)  # No dejar archivos vacíos o sin video útil.
            except OSError:
                pass
            log.warning("Clip descartado sin contenido reproducible: %s", self._path)
            return
        duration = time.monotonic() - self._clip_started
        log.info("Clip cerrado (%s): %s [%.1f s, %d frames]", reason, self._path, duration, self._frames_written)

    def _file_size(self) -> int:
        """Tamaño en bytes del clip actual (0 si no existe)."""
        try:
            return self._path.stat().st_size
        except OSError:
            return 0
