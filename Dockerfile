# UC7 Web-Demo: FastAPI + Claude Agent SDK (UC4-Agent).
# Das Python-SDK bringt die Claude-Code-CLI als fertige Binary mit, Node.js ist nicht nötig.
# Kein Alpine: Die gebündelte Binary braucht glibc.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Eigener Benutzer ohne Root-Rechte. Die CLI schreibt ihre Sitzungsdaten nach $HOME/.claude.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY uc4_agent ./uc4_agent
COPY app ./app

# Protokoll (SQLite) und Laufordner. Lokal per Volume dauerhaft, in Cloud Run später Neon statt SQLite.
ENV DATEN_DIR=/daten \
    PORT=8080
RUN mkdir -p /daten && chown app:app /daten
VOLUME ["/daten"]

USER app
EXPOSE 8080

# Cloud Run gibt den Port über $PORT vor, lokal gilt 8080.
CMD ["sh", "-c", "exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
