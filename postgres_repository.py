"""TaskRepository backed by Postgres, via psycopg 3 and raw SQL.

Same five methods as the SQLite implementation, same dict shape out, same
oldest-first ordering - which is what lets storage.py swap one for the other
without a single line of main.py changing.

No ORM on purpose. The SQL here sits next to the SQLite SQL and the differences
are visible: %s placeholders instead of ?, RETURNING instead of lastrowid, and a
real BOOLEAN column instead of 0/1.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from repository import TaskRepository

# Every query asks for the columns in this order, so one string keeps the
# SELECT list and the RETURNING clauses from drifting apart.
COLUMNS = "id, title, done"


class SchemaMissingError(RuntimeError):
    """Raised when the database is reachable but db/schema.sql never ran."""


class PostgresTaskRepository(TaskRepository):
    """Tasks stored in Postgres."""

    def __init__(self, conninfo: str, connect_timeout: float = 30.0):
        # A pool rather than the single locked connection SQLite needed. FastAPI
        # runs sync endpoints in a threadpool, so requests really are concurrent;
        # Postgres handles concurrent writers, so there is nothing to serialise
        # and each request can simply borrow its own connection.
        self._pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=10,
            open=True,
            # dict_row makes rows come back as {"id": ..., "title": ..., "done": ...},
            # which is already the API's response shape - so unlike SQLite there
            # is no row-to-dict helper and no bool cast, because `done` is a real
            # Postgres BOOLEAN and arrives as True or False.
            kwargs={"row_factory": dict_row},
        )
        # The app container starts the moment the db healthcheck passes, but a
        # restart can still race it. Waiting here turns "connection refused" at
        # the first request into a slightly slower, successful startup.
        self._pool.wait(timeout=connect_timeout)
        self._verify_schema()

    def _verify_schema(self) -> None:
        """Fail loudly and usefully if the tasks table is not there.

        The schema is created by db/schema.sql through Postgres's entrypoint,
        which only fires on a fresh data volume. When someone points the app at
        an older volume, the honest failure is this message at startup rather
        than an UndefinedTable error on whichever request happens to arrive first.
        """
        with self._pool.connection() as conn:
            (exists,) = conn.execute("SELECT to_regclass('public.tasks')").fetchone().values()
        if exists is None:
            raise SchemaMissingError(
                "Connected to Postgres, but there is no 'tasks' table. The schema "
                "is applied by db/schema.sql on first init of the data volume. "
                "Either recreate the volume with `docker compose down -v && docker "
                "compose up`, or apply it by hand with: docker compose exec -T db "
                "psql -U $POSTGRES_USER -d $POSTGRES_DB "
                "-f /docker-entrypoint-initdb.d/schema.sql"
            )

    def get_all(self) -> list[dict]:
        # ORDER BY id makes oldest-first a promise. Postgres is free to return
        # rows in any order without it, and more so than SQLite ever was.
        with self._pool.connection() as conn:
            return conn.execute(
                f"SELECT {COLUMNS} FROM tasks ORDER BY id"
            ).fetchall()

    def get_by_id(self, task_id: int) -> dict | None:
        # %s is a psycopg placeholder, not string formatting - the value is sent
        # to the server separately from the SQL text, so a crafted id can never
        # be executed as SQL.
        with self._pool.connection() as conn:
            return conn.execute(
                f"SELECT {COLUMNS} FROM tasks WHERE id = %s", (task_id,)
            ).fetchone()

    def create(self, title: str) -> dict:
        # RETURNING hands back the row Postgres just wrote, id and all, in the
        # same round trip as the INSERT. SQLite needed lastrowid plus a dict
        # built by hand; here the response is literally what was stored.
        with self._pool.connection() as conn:
            return conn.execute(
                f"INSERT INTO tasks (title, done) VALUES (%s, FALSE) "
                f"RETURNING {COLUMNS}",
                (title,),
            ).fetchone()

    def update(self, task_id: int, fields: dict) -> dict | None:
        # done arrives as a real Python bool and goes straight into a real
        # BOOLEAN column - no int() on the way in, no bool() on the way out.
        #
        # The column names are interpolated into the SQL, the values are not.
        # That is safe because the names can only ever be the literals "title"
        # and "done", chosen by the API's validation and never by the request.
        # Leaving an untouched column out of SET is what makes an omitted field
        # keep its stored value.
        assignments = ", ".join(f"{column} = %s" for column in fields)
        with self._pool.connection() as conn:
            return conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id = %s RETURNING {COLUMNS}",
                (*fields.values(), task_id),
            ).fetchone()

    def remove(self, task_id: int) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        # rowcount is 0 when the WHERE matched nothing, which is exactly the 404
        # case - so one statement does the lookup and the delete together.
        return cursor.rowcount > 0

    def close(self) -> None:
        self._pool.close()
