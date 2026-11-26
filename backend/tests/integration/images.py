"""Container images used by the integration tests and docker-compose.yml.

The PostgreSQL string is exactly the `image:` of the `postgres` service in
docker-compose.yml (tag and digest); tests/test_compose_config.py keeps them equal.
"""

POSTGRES_IMAGE = "postgres:17.12-bookworm@sha256:40865bc877a289da75963d97d72b59633f639fc5af7a4e856bfa3714cae5b536"
