# One Dockerfile for every service; pick the service with --build-arg SERVICE=orders
# Build from the repo root:  docker build -f services/Dockerfile --build-arg SERVICE=orders .
FROM python:3.12-slim

ARG SERVICE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_MODULE=${SERVICE}_service

WORKDIR /app

COPY services/${SERVICE}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/common ./common
COPY services/${SERVICE}/${SERVICE}_service ./${SERVICE}_service

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD uvicorn ${SERVICE_MODULE}.main:create_app --factory --host 0.0.0.0 --port 8000
