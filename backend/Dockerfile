FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# projects/ is mounted as a volume at runtime (see docker-compose.yml)
EXPOSE 8000

# start.sh runs "alembic upgrade head" first when TASK_STORE=db, then starts uvicorn.
CMD ["sh", "scripts/start.sh"]
