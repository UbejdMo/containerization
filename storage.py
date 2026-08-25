"""Picks which TaskRepository the app runs against.

This is the one and only place that names a concrete database class. main.py
asks for "a repository" and gets one, so moving the data to a different engine
is a change here and nowhere else - no route, no validation rule, no response
shape has to be touched.

Postgres is the active backend. The SQLite implementation is still in the tree
and still passes the same contract tests; it is what makes the interface more
than a claim. It is reachable only by asking for it explicitly, so a missing or
broken DATABASE_URL can never quietly drop the app onto a local file and look
like it worked.
"""

import os

from dotenv import load_dotenv

from repository import TaskRepository

# Reads .env from the project directory when the app runs straight on the host
# (uvicorn main:app). Inside Docker the variables are already in the
# environment, and load_dotenv never overwrites what is really set there.
load_dotenv()

BACKEND = os.getenv("TASKS_BACKEND", "postgres").strip().lower()

MISSING_URL = (
    "DATABASE_URL is not set, so there is nothing to connect to. Copy the "
    "template with `cp .env.example .env` and fill it in, or start the stack "
    "with `docker compose up`, which passes the variable in for you."
)


def get_repository() -> TaskRepository:
    """Build the repository the API should use."""
    if BACKEND == "sqlite":
        # Kept deliberately: a second implementation of the same interface,
        # opt-in via TASKS_BACKEND=sqlite, and never a silent fallback.
        from sqlite_repository import SQLiteTaskRepository

        return SQLiteTaskRepository()

    if BACKEND != "postgres":
        raise ValueError(
            f"Unknown TASKS_BACKEND {BACKEND!r}. Use 'postgres' (the default) "
            f"or 'sqlite'."
        )

    from postgres_repository import PostgresTaskRepository

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(MISSING_URL)
    return PostgresTaskRepository(url)
