# Task API — containerised, on Postgres

A small REST API for tracking tasks, built with FastAPI. It started as a CRUD API over
a Python list, then moved to SQLite, and now runs against **Postgres in Docker**. The
routes, request bodies, response shapes and status codes have not changed once across
any of those moves — only where the data lives did.

```bash
cp .env.example .env      # then edit it if the default ports are taken
docker compose up         # API on http://localhost:8000, docs at /docs
```

That is the whole setup. One command brings up the database and the app together.

## The point of this version

Storage sits behind an interface, so the database is an implementation detail:

```
main.py                    routes, validation, error shapes — and no SQL at all
  └── storage.py           picks the backend (the only file naming a concrete class)
        └── repository.py  TaskRepository: get_all, get_by_id, create, update, remove
              ├── postgres_repository.py   psycopg 3, raw SQL      ← active
              └── sqlite_repository.py     sqlite3, raw SQL        ← still passes
```

Two implementations of one interface, and the API cannot tell them apart. That is not a
claim — [`verify_api.py`](verify_api.py) is 51 checks written purely against HTTP
(status codes, bodies, error shapes, ordering, validation) and it passes against both:

```console
$ python verify_api.py                      # DATABASE_URL set -> Postgres
Verifying in-process against PostgresTaskRepository
51/51 checks passed

$ TASKS_BACKEND=sqlite python verify_api.py
Verifying in-process against SQLiteTaskRepository
51/51 checks passed

$ python verify_api.py http://localhost:8000   # the running containers
Verifying the API over HTTP at http://localhost:8000
51/51 checks passed
```

No ORM. The SQL is written by hand in both files, which is what makes the differences
between the two engines visible rather than hidden:

| | SQLite | Postgres |
|---|---|---|
| Placeholder | `?` | `%s` |
| Id after insert | `cursor.lastrowid` | `INSERT … RETURNING id, title, done` |
| `done` | no boolean type — stored `0`/`1`, cast back on read | a real `BOOLEAN`, no conversion at all |
| Concurrency | one connection behind a `threading.Lock` | a `psycopg_pool` connection pool |
| Id assignment | `INTEGER PRIMARY KEY` aliases `rowid`, reuses freed ids | `GENERATED ALWAYS AS IDENTITY`, never reuses |

That last row is a real behavioural difference and worth being straight about: after
deleting the highest task, SQLite hands the next insert the same id again and Postgres
does not. Nothing in the API ever promised otherwise — ids come from the database and
are integers, and that is all — but it is visible if you go looking.

## Did the service and routes really not change?

**Stage 4 changed `main.py` by zero lines.** Swapping SQLite for Postgres touched
`storage.py`, added `postgres_repository.py`, and nothing else:

```console
$ git diff f27dd6a -- main.py
$                                        # no output — not one line
```

**Stage 0 did change `main.py`, on purpose, and that is the honest part.** The Week 3
app had no repository layer at all: `main.py` opened the SQLite connection itself and
the route handlers called module-level helpers that ran SQL. There was no interface to
implement, so one had to be extracted first. The route bodies went from calling
`select_tasks()` to calling `repo.get_all()`, and the SQL moved out to
`sqlite_repository.py` — 121 lines deleted, 29 added, all of it mechanical.

What did **not** change, then or since: every path, every status code, every response
body, every error message, the validation rules, and the `422`-free OpenAPI document.
`verify_api.py` was written against the original behaviour and has passed unmodified
ever since.

One judgement call inside that refactor is worth naming. The old `PUT` handler did
`updates["done"] = int(payload["done"])` — a route deciding how a database spells
"true". That conversion moved into the SQLite repository, where it belongs, so the route
now hands over a real `bool`. Postgres stores it directly in a `BOOLEAN` column and does
nothing. Had that cast stayed in the route, the Postgres swap would have needed a route
edit, which is exactly the leak the interface exists to prevent.

## How the table gets created

The `tasks` table is defined once, in [`db/schema.sql`](db/schema.sql), and applied by
**Postgres's `docker-entrypoint-initdb.d` mount** rather than by an init step inside the
app. Compose mounts the file read-only into the container and the official `postgres`
image runs everything in that directory the first time the data volume is initialised.

That was the simpler of the two options: no DDL in Python, no "have I migrated yet?"
check on every startup, and the schema is a plain `.sql` file `psql` can read.

The trade-off worth knowing: **the entrypoint only fires on an empty data volume.** Edit
`db/schema.sql` against a volume that already exists and nothing happens. Either recreate
the volume with `docker compose down -v` (which deletes the data), or apply it by hand:

```bash
docker compose exec -T db psql -U tasks -d tasks -f /docker-entrypoint-initdb.d/schema.sql
```

Both statements in the file are guarded — `CREATE TABLE IF NOT EXISTS`, and a seed that
only inserts `WHERE NOT EXISTS (SELECT 1 FROM tasks)` — so applying it by hand is safe
and will not duplicate the example rows. If the table is somehow missing, the app says so
at startup with that command in the error message, rather than failing on whichever
request happens to arrive first.

## Persistence — how it was actually tested

Not asserted. Run, with the output pasted below.

**Setup.** Stack up via `docker compose up -d`, three tasks created through the API, and
one of them updated so the test covers an `UPDATE` and not just `INSERT`s:

```console
$ curl -s -X POST localhost:8000/tasks -H "Content-Type: application/json" -d '{"title":"Survive a restart"}'
{"id":10,"title":"Survive a restart","done":false}
$ curl -s -X POST localhost:8000/tasks -H "Content-Type: application/json" -d '{"title":"Written before the restart"}'
{"id":11,"title":"Written before the restart","done":false}
$ curl -s -X POST localhost:8000/tasks -H "Content-Type: application/json" -d '{"title":"Third one for good measure"}'
{"id":12,"title":"Third one for good measure","done":false}

$ curl -s -X PUT localhost:8000/tasks/10 -H "Content-Type: application/json" -d '{"done":true}'
{"id":10,"title":"Survive a restart","done":true}
```

(The ids start at 10 because `verify_api.py` had just consumed 4–9 and an IDENTITY
sequence does not reuse them.)

**Test 1 — restart both containers.**

```console
$ docker compose restart
 Container tasks-db Restarting
 Container tasks-api Restarting
 Container tasks-db Started
 Container tasks-api Started

$ curl -s localhost:8000/tasks
[{"id":1,"title":"Buy milk","done":false},{"id":2,"title":"Read a chapter of the FastAPI docs","done":true},{"id":3,"title":"Walk the dog","done":false},{"id":10,"title":"Survive a restart","done":true},{"id":11,"title":"Written before the restart","done":false},{"id":12,"title":"Third one for good measure","done":false}]
```

All six rows, and task 10 is still `done: true` — the update survived too.

**Test 2 — destroy the containers entirely and build new ones.** A restart reuses the
same containers, so it is the weaker test. `down` removes them completely; `up` creates
new ones against the same named volume:

```console
$ docker compose down
 Container tasks-api Removed
 Container tasks-db Removed
 Network containerize_your_stack_default Removed

$ docker compose up -d
 Container tasks-db Waiting
 Container tasks-db Healthy
 Container tasks-api Started

$ docker compose ps --format '{{.Name}}  {{.Status}}'
tasks-api  Up 3 seconds
tasks-db  Up 8 seconds (healthy)

$ curl -s localhost:8000/tasks
[{"id":1,"title":"Buy milk","done":false},{"id":2,"title":"Read a chapter of the FastAPI docs","done":true},{"id":3,"title":"Walk the dog","done":false},{"id":10,"title":"Survive a restart","done":true},{"id":11,"title":"Written before the restart","done":false},{"id":12,"title":"Third one for good measure","done":false}]
```

Byte-for-byte the same list, out of containers that did not exist a moment earlier.

**Test 3 — the negative control.** Both tests above would pass just as happily if
something other than the volume were doing the work, so here is the run that proves it is
the volume. `down -v` deletes it:

```console
$ docker compose down -v
 Volume containerize_your_stack_pgdata Removing
 Volume containerize_your_stack_pgdata Removed

$ docker compose up -d && curl -s localhost:8000/tasks
[{"id":1,"title":"Buy milk","done":false},{"id":2,"title":"Read a chapter of the FastAPI docs","done":true},{"id":3,"title":"Walk the dog","done":false}]
```

The three tasks are gone and the database is back to its seeded state, because
`db/schema.sql` ran again on a fresh volume. Data survives `restart` and `down`/`up`; it
does not survive `down -v`. That is the volume doing it, and nothing else.

## Configuration

Everything lives in `.env`, which is gitignored. [`.env.example`](.env.example) is the
committed template and contains no real credentials.

| Variable | What it is |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Read by the `postgres` image on first init; they create the role and database inside the volume. |
| `DATABASE_URL` | The connection string the app reads. In `.env` it points at `localhost` — the value a host-run `uvicorn main:app` needs. |
| `POSTGRES_HOST_PORT` | Host port for the database. Defaults to **5433**, not 5432 — see below. |
| `APP_HOST_PORT` | Host port for the API. Defaults to 8000. |
| `TASKS_BACKEND` | `postgres` (default) or `sqlite`. Opt-in only; there is no silent fallback. |

**Two addresses for one database.** Inside the compose network the app reaches Postgres
at `db:5432` — the service name, on its real port. From your machine it is
`localhost:${POSTGRES_HOST_PORT}`. The compose file therefore builds `DATABASE_URL` for
the app container itself out of the `POSTGRES_*` variables rather than reusing the one in
`.env`, because the two are genuinely different addresses and quietly sharing one would
break whichever context lost the coin toss.

**Why 5433.** 5432 is a popular port, and a natively installed PostgreSQL service will
already be listening on it. Docker publishes onto a contested port without complaining,
and connections then reach the *wrong server* — which shows up as
`password authentication failed`, not as a port conflict. It cost a debugging detour
here, so the host port is a variable and defaults out of the way.

## Running without Docker

The app still runs directly on the host, against the containerised database:

```bash
docker compose up -d db          # database only
pip install -r requirements.txt
uvicorn main:app --reload        # reads .env via python-dotenv
```

Or against SQLite, with no database server at all — `TASKS_BACKEND=sqlite uvicorn main:app`,
which creates `tasks.db` next to the code as it always did.

## Endpoints

| Method | Path | Description | Success | Errors |
|---|---|---|---|---|
| `GET` | `/` | Service name, version, and where to go next | `200` | — |
| `GET` | `/health` | Liveness probe for monitors and load balancers | `200` | — |
| `GET` | `/tasks` | Every task, oldest first | `200` | — |
| `GET` | `/tasks/{id}` | One task by id | `200` | `400` non-integer id, `404` unknown id |
| `POST` | `/tasks` | Create a task from `{"title": "..."}` | `201` | `400` missing/empty/non-string title |
| `PUT` | `/tasks/{id}` | Update `title`, `done`, or both | `200` | `400` empty body or bad field, `404` unknown id |
| `DELETE` | `/tasks/{id}` | Remove a task; response body is empty | `204` | `404` unknown id |

The task object is `{"id": 1, "title": "Buy milk", "done": false}`. Every failure — all of
them, no exceptions — is `{"error": "..."}`. Interactive docs are at `/docs`.

### Rules the server enforces

1. A new task's `id` comes from the database.
2. A new task always starts `done: false`; a `done` sent on create is ignored.
3. A `title` that is missing, empty, whitespace-only, or not a string is `400`, and
   nothing is written.
4. `PUT` accepts `title`, `done`, or both — whatever you leave out keeps its current
   value, because only the columns you sent appear in the `SET` clause. `{}` is a `400`.
5. `DELETE` returns `204` with a genuinely empty body.

## What I learned

**An interface is only real once a second implementation exists.** The Week 3 README said
moving to Postgres would be "a change of driver and connection string". That turned out
to be true only after Stage 0, and Stage 0 was the work — once storage was genuinely
behind five methods, the Postgres class wrote itself and `main.py` never had to be opened.

**Leaks hide in type conversions.** The one place the abstraction nearly failed was
`int(payload["done"])` sitting in a route. It looks like validation and is actually
storage knowledge. Anything that knows how a *particular* database spells a value belongs
below the interface.

**`depends_on` alone is a race.** Postgres accepts TCP connections before it will answer
queries, so a bare `depends_on: [db]` starts the app into a database that is not ready
yet. `condition: service_healthy` against a `pg_isready` healthcheck is what makes
`docker compose up` reliable from cold — the log shows `tasks-db Waiting` → `Healthy` →
`tasks-api Starting`, in that order.

**A volume is the difference between a database and a scratch pad.** Without it, Postgres
writes into the container's own layer and `docker compose down` takes the data with it.
The negative control above is the part that actually proves which one you have.

**Test the contract, not the code.** `verify_api.py` only knows HTTP. That is precisely
why it could be run against SQLite, against Postgres, and against the containers over the
network, and mean the same thing all three times.

## History

The previous version, on SQLite: <https://github.com/UbejdMo/connecting_to_db>. The
SQLite-era notes on poking at the database with a GUI client are still in
[`docs/sql-exploration.md`](docs/sql-exploration.md), and that backend still works.
