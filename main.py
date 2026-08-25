"""Task API - Week 2 assignment.

A tiny CRUD API over a SQLite database, documented at /docs.
Every error answers {"error": "..."}, validation failures are 400 (never
FastAPI's default 422), and DELETE returns a bare 204.

Run it with:  uvicorn main:app --reload
"""

import sqlite3
import threading
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="Task API",
    description=(
        "A minimal task tracker, now backed by SQLite.\n\n"
        "Tasks live in a tasks.db file next to the app, so **the data is still "
        "there after a restart**. The file is created, and seeded with three "
        "example tasks, the first time the server runs.\n\n"
        "Errors always come back as a JSON object with a single *error* key, "
        "and invalid input is 400, never 422."
    ),
    version="1.0",
)

# Where the database lives: tasks.db, right next to this file at the repo root.
# Resolving it from __file__ instead of the current working directory means
# `uvicorn main:app` finds the same file no matter which folder it is run from.
DB_PATH = Path(__file__).with_name("tasks.db")

# The rows a brand-new database starts life with, as (title, done) pairs. They
# are inserted exactly once - on the very first run - and never again.
SEED_TASKS = [
    ("Buy milk", 0),
    ("Read a chapter of the FastAPI docs", 1),
    ("Walk the dog", 0),
]

# check_same_thread=False because FastAPI runs sync endpoints in a threadpool,
# so this connection gets touched by more than one thread. SQLite is fine with
# that as long as the calls are serialised, which is what db_lock is for.
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db() -> None:
    """Create the tasks table if it is missing, then seed it once.

    Both halves are guarded so that restarting the server is harmless:
    CREATE TABLE IF NOT EXISTS does nothing on run two, and the seed only fires
    when the table is genuinely empty. That is why the example tasks show up
    once, ever, instead of three more of them every time the process starts.

    id is a plain INTEGER PRIMARY KEY rather than AUTOINCREMENT on purpose - it
    aliases SQLite's rowid, so an insert gets max(id) + 1, which is the same id
    rule Assignment 1 used.
    """
    with db_lock, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id    INTEGER PRIMARY KEY,
                title TEXT    NOT NULL,
                done  BOOLEAN NOT NULL DEFAULT 0
            )
            """
        )
        (count,) = db.execute("SELECT COUNT(*) FROM tasks").fetchone()
        if count == 0:
            db.executemany("INSERT INTO tasks (title, done) VALUES (?, ?)", SEED_TASKS)


init_db()

TITLE_ERROR = "title is required and must be a non-empty string"
DONE_ERROR = "done must be true or false"
EMPTY_UPDATE_ERROR = "send at least one of title or done"


class Task(BaseModel):
    """One task, exactly as it appears in every successful response."""

    id: int
    title: str
    done: bool


class Error(BaseModel):
    """The one and only error shape this API produces."""

    error: str


# Reusable OpenAPI blocks so each route documents its failures without repeating
# the schema. These describe responses; they do not enforce anything.
NOT_FOUND = {"model": Error, "description": "No task with that id"}
BAD_REQUEST = {"model": Error, "description": "The request body failed validation"}


@app.exception_handler(HTTPException)
def http_exception_handler(request, exc: HTTPException):
    """Rewrite every HTTPException into the graded error shape.

    Raising HTTPException(404, "Task 99 not found") would normally produce
    {"detail": "Task 99 not found"}. This handler intercepts it and emits
    {"error": "Task 99 not found"} instead, so route code stays readable.
    """
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request, exc: RequestValidationError):
    """Catch what FastAPI itself rejects before a route ever runs.

    Unparseable JSON, a missing body, or /tasks/abc never reach my code -
    FastAPI raises RequestValidationError and answers 422 with a list under
    "detail". The contract says 400 and {"error": ...}, so translate here.
    """
    first = exc.errors()[0]
    # loc looks like ("path", "task_id") or ("body", 0); keep only the named
    # parts so the client sees "task_id: ...", never a character offset.
    named = [part for part in first["loc"] if isinstance(part, str)]
    field = ".".join(part for part in named if part not in ("body", "query", "path"))
    message = f"{field}: {first['msg']}" if field else f"invalid request body: {first['msg']}"
    return JSONResponse(status_code=400, content={"error": message})


def row_to_task(row: sqlite3.Row) -> dict:
    """Turn one database row into the exact JSON shape the API promises.

    SQLite has no real boolean type - done comes back as 0 or 1 - so the cast
    to True/False happens here, in one place, instead of in every route.
    """
    return {"id": row["id"], "title": row["title"], "done": bool(row["done"])}


def select_tasks() -> list[dict]:
    """Every task, oldest first. ORDER BY id makes that ordering a promise
    rather than something SQLite happens to do today."""
    with db_lock:
        rows = db.execute("SELECT id, title, done FROM tasks ORDER BY id").fetchall()
    return [row_to_task(row) for row in rows]


def select_task(task_id: int) -> dict | None:
    """One task by id, or None if the database has no such row.

    The id goes in as a ? parameter, never string-formatted into the SQL -
    that is what stops a crafted id from being executed as SQL.
    """
    with db_lock:
        row = db.execute(
            "SELECT id, title, done FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return row_to_task(row) if row else None


def clean_title(payload: dict) -> str:
    """Validate the client's title and hand back a trimmed one.

    The server never trusts the client: the key may be absent, be null, be a
    number, or be nothing but spaces. Every one of those is a 400.
    """
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail=TITLE_ERROR)
    return title.strip()


@app.get(
    "/",
    tags=["meta"],
    summary="Service information",
    description="Name, version, and where to find the interesting endpoints.",
)
def read_root():
    return {"name": "Task API", "version": "1.0", "endpoints": ["/tasks"]}


@app.get(
    "/health",
    tags=["meta"],
    summary="Liveness probe",
    description="Returns 200 as long as the process is up. Cheap on purpose - "
    "monitors and load balancers hit this every few seconds.",
)
def health():
    return {"status": "ok"}


@app.get(
    "/tasks",
    response_model=list[Task],
    tags=["tasks"],
    summary="List every task",
    description="Returns all tasks, oldest first, straight from SQLite. The "
    "list is never paginated.",
)
def list_tasks():
    return select_tasks()


@app.get(
    "/tasks/{task_id}",
    response_model=Task,
    responses={404: NOT_FOUND, 400: BAD_REQUEST},
    tags=["tasks"],
    summary="Get one task",
    description="Looks a task up by id. A non-integer id is a 400; an unknown "
    "integer id is a 404.",
)
def get_task(task_id: int):
    task = select_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@app.post(
    "/tasks",
    status_code=201,
    response_model=Task,
    responses={400: BAD_REQUEST},
    tags=["tasks"],
    summary="Create a task",
    description="Send a title. SQLite assigns the id and the task always starts "
    "at done=false - a done sent by the client is ignored.",
)
def create_task(payload: dict = Body(..., examples=[{"title": "Buy milk"}])):
    # Validate first: a bad title must be a 400 without ever touching the
    # database, so a rejected request leaves no trace behind.
    title = clean_title(payload)

    with db_lock, db:
        cursor = db.execute("INSERT INTO tasks (title, done) VALUES (?, 0)", (title,))

    # lastrowid is the id SQLite just handed out, so there is no need to read
    # the table back or to compute max(id) + 1 by hand the way the in-memory
    # version had to.
    return {"id": cursor.lastrowid, "title": title, "done": False}


@app.put(
    "/tasks/{task_id}",
    response_model=Task,
    responses={404: NOT_FOUND, 400: BAD_REQUEST},
    tags=["tasks"],
    summary="Update a task",
    description="Send title, done, or both. Whatever you leave out keeps its "
    "current value, so this is really a partial update. An empty object is a 400.",
)
def update_task(
    task_id: int,
    payload: dict = Body(..., examples=[{"title": "Buy oat milk", "done": True}]),
):
    if select_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # Both fields are optional, but an empty body means the client asked for
    # nothing - that is a mistake worth reporting, not a silent no-op.
    if "title" not in payload and "done" not in payload:
        raise HTTPException(status_code=400, detail=EMPTY_UPDATE_ERROR)

    # Collect only the columns the client actually sent. Everything is checked
    # before a single row is touched, so a request that is going to be a 400
    # never half-applies itself.
    updates: dict[str, object] = {}
    if "title" in payload:
        updates["title"] = clean_title(payload)
    if "done" in payload:
        if not isinstance(payload["done"], bool):
            raise HTTPException(status_code=400, detail=DONE_ERROR)
        # SQLite has no boolean type, so True/False is stored as 1/0.
        updates["done"] = int(payload["done"])

    # The column names are interpolated into the SQL, the values are not. That
    # is safe because the names can only ever be the literals "title" and
    # "done" - nothing the client sends reaches the SQL text. Leaving the
    # untouched column out of SET is what makes an omitted field keep its
    # stored value instead of being overwritten.
    assignments = ", ".join(f"{column} = ?" for column in updates)
    with db_lock, db:
        db.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?",
            (*updates.values(), task_id),
        )

    # Read the row back instead of echoing the request, so the response is
    # what the database actually holds.
    return select_task(task_id)


@app.delete(
    "/tasks/{task_id}",
    status_code=204,
    response_class=Response,
    responses={204: {"description": "Deleted. The body is empty."}, 404: NOT_FOUND},
    tags=["tasks"],
    summary="Delete a task",
    description="Removes the task and returns 204 with no body at all.",
)
def delete_task(task_id: int):
    with db_lock, db:
        cursor = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    # rowcount is 0 when the WHERE matched nothing, which is exactly the 404
    # case - so one statement does the lookup and the delete together.
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    # 204 means "done, and there is deliberately nothing to send back".
    # Returning a dict here would make FastAPI write a body that contradicts
    # the status line, so hand back a bodyless Response instead.
    return Response(status_code=204)


def custom_openapi():
    """Generate the OpenAPI document, minus the 422s this API never sends.

    FastAPI adds a 422 response to every route that takes a parameter, because
    that is what it would normally return for bad input. The validation handler
    above turns those into 400s, so leaving 422 in the document would document
    a status code that can never actually arrive.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    for operations in schema["paths"].values():
        for operation in operations.values():
            operation["responses"].pop("422", None)
    for unused in ("HTTPValidationError", "ValidationError"):
        schema["components"]["schemas"].pop(unused, None)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
