FROM python:3.11-alpine

LABEL maintainer="Celestials316" \
      description="Docker container monitoring with Telegram notifications (Python version)" \
      version="5.0.0"

RUN apk add --no-cache \
    docker-cli \
    curl \
    tzdata \
    && rm -rf /var/cache/apk/*

ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY scripts/monitor.py /app/monitor.py
COPY scripts/healthcheck.py /app/healthcheck.py

RUN pip install --no-cache-dir requests

RUN mkdir -p /data

HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=15s \
    CMD ["python3", "/app/healthcheck.py"]

ENTRYPOINT ["python3"]
CMD ["/app/monitor.py"]
