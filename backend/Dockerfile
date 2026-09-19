FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* && groupadd -g 10001 autopilot && useradd -u 10001 -g 10001 -m autopilot
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app
COPY services /services
RUN mkdir -p /data/artifacts && chown -R 10001:10001 /data /app
USER 10001:10001
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]

FROM runtime AS test
USER root
COPY backend/requirements-dev.txt /app/requirements-dev.txt
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements-dev.txt
USER 10001:10001
ENV DATABASE_URL=sqlite:////tmp/autopilot-test.db OTEL_EXPORTER_OTLP_ENDPOINT="" APP_ENVIRONMENT=test
CMD ["pytest","-q"]
