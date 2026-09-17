# Cloud API, workers and deploy task share this image; the ECS task definition
# picks the command (terraform/app/ecs.tf). Built for linux/arm64 (Fargate Graviton).
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements-cloud.txt .
RUN pip install -r requirements-cloud.txt

COPY alembic.ini .
COPY cloud_api/ cloud_api/
COPY db/ db/
COPY analysis/ analysis/
COPY judge/ judge/
COPY resume/ resume/

RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000
CMD ["gunicorn", "cloud_api.wsgi:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "150", "--access-logfile", "-"]
