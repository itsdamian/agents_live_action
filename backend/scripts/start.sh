#!/bin/sh
# Container entrypoint: migrate the database (db mode only), then start the API.
set -e

if [ "${TASK_STORE:-file}" = "db" ]; then
    if [ -f alembic.ini ]; then
        echo "TASK_STORE=db: running database migrations"
        alembic upgrade head
    else
        echo "TASK_STORE=db: no alembic.ini yet (migrations arrive with the schema task); skipping"
    fi
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000
