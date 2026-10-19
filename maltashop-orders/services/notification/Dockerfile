# syntax=docker/dockerfile:1
# ---- Stage 1: build (compilers and caches stay here, not in the runtime image) ----
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: runtime (small, non-root) ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SERVICE_NAME=notification PORT=8004
RUN useradd --system --uid 10001 --no-create-home app && mkdir /data && chown app /data
WORKDIR /app
COPY --from=build /install /usr/local
COPY common ./common
COPY services/notification ./services/notification
USER app
EXPOSE 8004
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://localhost:'+os.environ['PORT']+'/health')"
CMD ["sh", "-c", "uvicorn services.notification.main:app --host 0.0.0.0 --port $PORT"]
