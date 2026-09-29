#!/bin/sh
# Apply database migrations, then start the given command (the API server by default).
set -e
alembic upgrade head
exec "$@"
