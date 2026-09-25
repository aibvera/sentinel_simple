"""Orquestador del sistema de videovigilancia: conecta los componentes y corre el loop eterno."""

import logging
import time

from alerts import CameraAlert
from camera import Camera
from config import load_config, setup_logging
from lifecycle import Watchdog, install_signal_handlers
from motion import MotionDetector
from recorder import Recorder
from storage import Storage

log = logging.getLogger("main")


def main() -> None:
    """Inicializa los componentes y ejecuta el ciclo captura → detección → grabación → limpieza."""
    config = load_config()
    setup_logging(config)
    log.info("Iniciando videovigilancia (DISIMIL_TH=%.2f%%, videos en %s)", config.disimil_th, config.save_path)

    stop_event = install_signal_handlers()
    watchdog = Watchdog(config.watchdog_seconds)
    camera = Camera(config)
    detector = MotionDetector(config)
    storage = Storage(config)
    recorder = Recorder(config, storage)
    alert = CameraAlert(config)

    try:
        while not stop_event.is_set():
            try:
                watchdog.beat()
                frame = camera.read()
                alert.update(frame is not None)
                motion = detector.detect(frame)
                recorder.update(frame, motion, camera.fps)
                storage.maintain()
            except Exception:
                # Última línea de defensa: un error inesperado no debe detener la vigilancia.
                log.exception("Error inesperado en el loop principal")
                time.sleep(1)
    finally:
        recorder.close()
        camera.release()
        watchdog.stop()
        log.info("Videovigilancia detenida")


if __name__ == "__main__":
    main()
