"""Alertas por Telegram cuando se pierde (y se recupera) la comunicación con la cámara."""

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from config import Config

log = logging.getLogger("alerts")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
HTTP_TIMEOUT_SECONDS = 15
# Si no hay internet, se reintenta el envío con espera creciente hasta este máximo.
MAX_RETRY_SECONDS = 300


class TelegramNotifier:
    """Envía mensajes desde un hilo propio para no bloquear el loop principal, reintentando si falla la red."""

    def __init__(self, token: str, chat_id: str):
        """Arranca el hilo que despacha la cola de mensajes."""
        self._url = API_URL.format(token=token)
        self._chat_id = chat_id
        self._queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, text: str) -> None:
        """Encola un mensaje para enviarlo apenas sea posible."""
        self._queue.put(text)

    def _run(self) -> None:
        """Envía los mensajes en orden; cada uno se reintenta hasta que Telegram lo acepte."""
        while True:
            text = self._queue.get()
            delay = 5.0
            while not self._deliver(text):
                time.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_SECONDS)

    def _deliver(self, text: str) -> bool:
        """Intenta un envío; devuelve False solo si vale la pena reintentar."""
        data = urllib.parse.urlencode({"chat_id": self._chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(self._url, data=data, timeout=HTTP_TIMEOUT_SECONDS):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                log.warning("Telegram no disponible (HTTP %d); se reintentará", exc.code)
                return False
            # Error de configuración (token o chat id): reintentar no sirve, se descarta el mensaje.
            log.error("Telegram rechazó el mensaje (HTTP %d: %s); revisa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID",
                      exc.code, _describe(exc))
            return True
        except Exception as exc:
            log.warning("No se pudo contactar a Telegram (%s); se reintentará", exc)
            return False
        log.info("Alerta enviada por Telegram")
        return True


class CameraAlert:
    """Avisa si la cámara lleva TELEGRAM_ALERT_MINUTES sin entregar frames, y otra vez cuando vuelve."""

    def __init__(self, config: Config):
        """Activa las alertas solo si hay token y chat id configurados."""
        self._config = config
        self._timeout = config.telegram_alert_minutes * 60
        self._last_ok = time.monotonic()
        self._alerted = False
        self._notifier = None
        if config.telegram_bot_token:
            self._notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
            log.info("Alertas por Telegram activas (cámara sin señal por %.0f min)", config.telegram_alert_minutes)

    def update(self, camera_ok: bool) -> None:
        """Registra si en este ciclo llegó un frame y envía la alerta o la recuperación cuando corresponde."""
        if self._notifier is None:
            return
        now = time.monotonic()
        if camera_ok:
            if self._alerted:
                self._alerted = False
                self._notifier.send(f"✅ Sentinel: la cámara volvió a las {self._config.now():%H:%M} "
                                    f"tras {_duration(now - self._last_ok)} sin comunicación.")
            self._last_ok = now
        elif not self._alerted and now - self._last_ok >= self._timeout:
            self._alerted = True
            since = self._config.now() - timedelta(seconds=now - self._last_ok)
            log.warning("Sin comunicación con la cámara desde %s; se avisa por Telegram", f"{since:%H:%M}")
            self._notifier.send(f"⚠️ Sentinel: sin comunicación con la cámara desde el {since:%Y-%m-%d} "
                                f"a las {since:%H:%M} ({_duration(now - self._last_ok)}).")


def _duration(seconds: float) -> str:
    """Formatea una duración en minutos u horas legibles."""
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _describe(exc: urllib.error.HTTPError) -> str:
    """Extrae la descripción del error que devuelve la API de Telegram."""
    try:
        return json.loads(exc.read())["description"]
    except Exception:
        return str(exc.reason)
