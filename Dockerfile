FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAVE_PATH=/recordings

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Usuario sin privilegios; /recordings se monta desde una carpeta del sistema anfitrión.
RUN useradd --uid 1000 --no-create-home sentinel \
    && mkdir -p /recordings \
    && chown sentinel /recordings
USER sentinel
VOLUME ["/recordings"]

CMD ["python", "main.py"]
