# One image, one origin: the frontend exported to static files and served by
# FastAPI at /, the API at /api, and the cached races baked in.
#
#   docker build -t f1-battles .
#   docker run -p 8000:8000 f1-battles        # http://localhost:8000
#
# The Vercel deployment is two projects instead (docs/DEPLOY-VERCEL.md); this
# is the container path - docker compose, or any host that runs an image.

# --- frontend: export the dashboard as static files ------------------------
FROM node:18-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Same origin as the API, so the base URL is empty. NEXT_PUBLIC_* is inlined
# at build time, which is why it is set here rather than at runtime.
ENV NEXT_OUTPUT=export \
    NEXT_PUBLIC_API_BASE_URL= \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- app: the backend, the races and the exported site ---------------------
FROM python:3.11-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
COPY backend/data/sessions/ ./data/sessions/
COPY --from=frontend /frontend/out/ ./static/

ENV DATA_MODE=replay \
    FRONTEND_DIR=/app/static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
