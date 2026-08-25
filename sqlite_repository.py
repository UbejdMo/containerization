"""TaskRepository backed by SQLite - the Week 3 storage layer, unchanged.

The SQL, the seeding rule and the threading guard are lifted verbatim out of
main.py; the only thing that is new is the class wrapped around them. Keeping
this file working after the Postgres swap is what proves the interface is real:
two databases, one contract, and the API cannot tell them apart.
"""

import sqlite3
import threading
from pathlib import Path

from repository import TaskRepository

# tasks.db, right next to this file at the repo root. Resolving it from __file__
# instead of the current working directory means `uvicorn main:app` finds the
# same file no matter which folder it is run from.
DB_PATH = Path(__file__).with_name("tasks.db")

# The rows a brand-new database starts life with, as (title, done) pairs. They
# are inserted exactly once - on the very first run - and never again.
SEED_TASKS = [
    ("Buy milk", 0),
    ("Read a chapter of the FastAPI docs", 1),
    ("Walk the dog", 0),
]


class SQLiteTaskRepository(TaskRepository):
    """Tasks stored in a local SQLite file."""

    def __init__(self, db_path: Path = DB_PATH):
        # check_same_thread=False because FastAPI runs sync endpoints in a
        # threadpool, so this connection gets touched by more than one thread.
        # SQLite is fine with that as long as the calls are serialised, which is
        # what the lock is for.
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the tasks table if it is missing, then seed it once.

        Both halves are guarded so that restarting the server is harmless:
        CREATE TABLE IF NOT EXISTS does nothing on run two, and the seed only
        fires when the table is genuinely empty.

        id is a plain INTEGER PRIMARY KEY rather than AUTOINCREMENT on purpose -
        it aliases SQLite's rowid, so an insert gets max(id) + 1.
        """
        with self._lock, self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id    INTEGER PRIMARY KEY,
                    title TEXT    NOT NULL,
                    done  BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
            (count,) = self._db.execute("SELECT COUNT(*) FROM tasks").fetchone()
            if count == 0:
                self._db.executemany(
                    "INSERT INTO tasks (title, done) VALUES (?, ?)", SEED_TASKS
                )

    @staticmethod
    def _to_task(row: sqlite3.Row) -> dict:
        """Turn one database row into the exact JSON shape the API promises.

        SQLite has no real boolean type - done comes back as 0 or 1 - so the
        cast to True/False happens here, in one place, and never in a route.
        """
        return {"id": row["id"], "title": row["title"], "done": bool(row["done"])}

    def get_all(self) -> list[dict]:
        # ORDER BY id makes oldest-first a promise rather than something SQLite
        # happens to do today.
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, done FROM tasks ORDER BY id"
            ).fetchall()
        return [self._to_task(row) for row in rows]

    def get_by_id(self, task_id: int) -> dict | None:
        # The id goes in as a ? parameter, never string-formatted into the SQL -
        # that is what stops a crafted id from being executed as SQL.
        with self._lock:
            row = self._db.execute(
                "SELECT id, title, done FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._to_task(row) if row else None

    def create(self, title: str) -> dict:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO tasks (title, done) VALUES (?, 0)", (title,)
            )
        # lastrowid is the id SQLite just handed out, so there is no need to
        # read the table back or to compute max(id) + 1 by hand.
        return {"id": cursor.lastrowid, "title": title, "done": False}

    def update(self, task_id: int, fields: dict) -> dict | None:
        # done arrives as a real bool from the API; SQLite has no boolean type,
        # so storing it as 1/0 is this class's business.
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]

        # The column names are interpolated into the SQL, the values are not.
        # That is safe because the names can only ever be the literals "title"
        # and "done" - nothing the client sends reaches the SQL text. Leaving
        # the untouched column out of SET is what makes an omitted field keep
        # its stored value instead of being overwritten.
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self._lock, self._db:
            cursor = self._db.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?", (*values, task_id)
            )
        if cursor.rowcount == 0:
            return None
        # Read the row back instead of echoing the request, so the response is
        # what the database actually holds.
        return self.get_by_id(task_id)

    def remove(self, task_id: int) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        # rowcount is 0 when the WHERE matched nothing, which is exactly the
        # 404 case - so one statement does the lookup and the delete together.
        return cursor.rowcount > 0
