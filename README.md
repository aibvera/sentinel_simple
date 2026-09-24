# Sentinel simple

Videovigilancia por detección de movimiento sobre una cámara RTSP. Graba clips mp4 (H.264) solo cuando hay movimiento, organizados en una carpeta por día, y borra automáticamente los videos antiguos.

## Componentes

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Orquestador: inicializa todo y corre el loop eterno |
| `config.py` | Lee y valida las variables del `.env` |
| `camera.py` | Conexión RTSP con timeouts y reconexión automática (backoff exponencial) |
| `motion.py` | % de disimilitud contra el frame anterior; hay movimiento si supera `DISIMIL_TH` |
| `recorder.py` | Abre/cierra clips con pre y post grabación, rota clips largos y se recupera de fallas de ffmpeg o del disco |
| `storage.py` | Carpetas `AAAA-MM-DD/`, borrado por `ELIMINATION_DAYS` y por falta de espacio (`MIN_FREE_MB`) |
| `lifecycle.py` | Apagado ordenado (Ctrl+C / `docker stop`) y watchdog que reinicia el proceso si se congela |

Los mp4 son **fragmentados**: si el proceso muere a mitad de una grabación, el archivo sigue siendo reproducible (se pierden como máximo ~2 s).

## Configuración

Copia `.env.example` a `.env` y ajusta los valores (cada variable está comentada ahí).

Para calibrar `DISIMIL_TH` usa `LOG_LEVEL=DEBUG`: cada frame con movimiento muestra su % de disimilitud.

## Ejecución local

```bash
.venv/Scripts/python.exe main.py
```

El `.venv` ya tiene las dependencias. `requirements.txt` usa `opencv-python-headless` (sin ventanas, más liviano para Docker); en el `.venv` se mantiene `opencv-python` porque `view_camera.py` necesita mostrar video. No instales ambos en el mismo entorno.

## Docker

Construir la imagen:

```bash
docker build -t sentinel-simple .
```

Ejecutar montando una carpeta del sistema anfitrión en `/recordings`, que es donde quedan los videos:

**Linux** (crea la carpeta antes; `--user` hace que los archivos queden a tu nombre):

```bash
mkdir -p ~/sentinel-videos && docker run -d --name sentinel --restart unless-stopped --init --user "$(id -u):$(id -g)" --env-file .env -e SAVE_PATH=/recordings -v ~/sentinel-videos:/recordings sentinel-simple
```

**macOS**:

```bash
docker run -d --name sentinel --restart unless-stopped --init --env-file .env -e SAVE_PATH=/recordings -v ~/sentinel-videos:/recordings sentinel-simple
```

**Windows (PowerShell)**:

```powershell
docker run -d --name sentinel --restart unless-stopped --init --env-file .env -e SAVE_PATH=/recordings -v "C:\sentinel-videos:/recordings" sentinel-simple
```

- `--restart unless-stopped` levanta el contenedor otra vez si el watchdog lo detiene o si se reinicia el equipo.
- `-e SAVE_PATH=/recordings` reemplaza el valor del `.env`, que es la ruta para ejecución local.
- El `.env` nunca se copia dentro de la imagen; se pasa al ejecutar con `--env-file`.

Ver logs y detener:

```bash
docker logs -f sentinel
```

```bash
docker stop sentinel
```

## Adicionales

Descargar carpeta de videos desde linux a pc windows:

```bash
scp -r abueno@IP_DEL_SERVIDOR:~/sentinel-videos/2026-09-23 "$env:USERPROFILE\Downloads\"
```
Reemplaza IP_DEL_SERVIDOR y la fecha del folder a descargar por los valores reales.
