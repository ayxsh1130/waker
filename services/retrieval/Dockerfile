FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models TOKENIZERS_PARALLELISM=false ANONYMIZED_TELEMETRY=false
RUN groupadd -g 10001 autopilot && useradd -u 10001 -g 10001 -m autopilot
WORKDIR /service
COPY services/retrieval/requirements.txt ./
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt
COPY services/retrieval/main.py ./main.py
RUN mkdir -p /data/chroma /models && chown -R 10001:10001 /data /models /service
USER 10001:10001
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8081"]
