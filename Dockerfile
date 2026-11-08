FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install . && mkdir -p /app/data

ENTRYPOINT ["tp2v"]
CMD ["--config", "/app/config.toml"]
