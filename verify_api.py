"""Checks the API contract against whichever repository is currently active.

This is the checkpoint script for the Postgres swap. It is deliberately written
against HTTP - status codes, bodies, error shapes - and knows nothing about
databases, so running it green on SQLite and green again on Postgres is the
evidence that the storage layer really is interchangeable.

Two ways to run it:

    python verify_api.py            # in-process, against the configured backend
    python verify_api.py http://localhost:8000   # against a running server

It creates a handful of tasks and deletes every one of them again, so it is
safe to point at a live database.
"""

import sys

failures: list[str] = []
checks = 0


def check(label: str, got, want) -> None:
    """Record one assertion instead of stopping at the first failure."""
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}\n      expected: {want!r}\n      got:      {got!r}")


def run(client) -> None:
    created: list[int] = []

    # --- meta ---------------------------------------------------------------
    r = client.get("/")
    check("GET / status", r.status_code, 200)
    check("GET / body", r.json(), {"name": "Task API", "version": "1.0",
                                   "endpoints": ["/tasks"]})
    check("GET /health", client.get("/health").json(), {"status": "ok"})

    # --- create -------------------------------------------------------------
    r = client.post("/tasks", json={"title": "Contract check"})
    check("POST /tasks status", r.status_code, 201)
    task = r.json()
    created.append(task["id"])
    check("POST /tasks title", task["title"], "Contract check")
    check("POST /tasks starts not done", task["done"], False)
    check("POST /tasks id is an int", isinstance(task["id"], int), True)

    # A done sent on create is ignored - the task always starts false.
    r = client.post("/tasks", json={"title": "Ignores done", "done": True})
    created.append(r.json()["id"])
    check("POST ignores client done", r.json()["done"], False)

    # The title is stored trimmed.
    r = client.post("/tasks", json={"title": "  padded  "})
    created.append(r.json()["id"])
    check("POST trims the title", r.json()["title"], "padded")

    # --- create validation --------------------------------------------------
    err = "title is required and must be a non-empty string"
    for label, body in [("missing", {}), ("empty", {"title": ""}),
                        ("whitespace", {"title": "   "}), ("number", {"title": 5}),
                        ("null", {"title": None})]:
        r = client.post("/tasks", json=body)
        check(f"POST bad title ({label}) status", r.status_code, 400)
        check(f"POST bad title ({label}) body", r.json(), {"error": err})

    # --- read ---------------------------------------------------------------
    first = created[0]
    r = client.get(f"/tasks/{first}")
    check("GET /tasks/{id} status", r.status_code, 200)
    check("GET /tasks/{id} body", r.json(),
          {"id": first, "title": "Contract check", "done": False})

    r = client.get("/tasks/999999")
    check("GET unknown id status", r.status_code, 404)
    check("GET unknown id body", r.json(), {"error": "Task 999999 not found"})

    # A non-integer id is a 400, never FastAPI's default 422.
    r = client.get("/tasks/abc")
    check("GET non-integer id status", r.status_code, 400)
    check("GET non-integer id is an error object", list(r.json()), ["error"])

    r = client.get("/tasks")
    check("GET /tasks status", r.status_code, 200)
    ids = [t["id"] for t in r.json()]
    check("GET /tasks is ordered by id", ids, sorted(ids))
    check("GET /tasks contains what was created",
          all(i in ids for i in created), True)
    check("GET /tasks booleans are real bools",
          all(isinstance(t["done"], bool) for t in r.json()), True)

    # --- update -------------------------------------------------------------
    r = client.put(f"/tasks/{first}", json={"done": True})
    check("PUT done only status", r.status_code, 200)
    check("PUT done only keeps the title", r.json(),
          {"id": first, "title": "Contract check", "done": True})

    r = client.put(f"/tasks/{first}", json={"title": "Renamed"})
    check("PUT title only keeps done", r.json(),
          {"id": first, "title": "Renamed", "done": True})

    r = client.put(f"/tasks/{first}", json={"title": "Both", "done": False})
    check("PUT both fields", r.json(),
          {"id": first, "title": "Both", "done": False})

    # The write really landed, rather than the response echoing the request.
    check("PUT persisted", client.get(f"/tasks/{first}").json(),
          {"id": first, "title": "Both", "done": False})

    # --- update validation --------------------------------------------------
    r = client.put(f"/tasks/{first}", json={})
    check("PUT empty body status", r.status_code, 400)
    check("PUT empty body message", r.json(),
          {"error": "send at least one of title or done"})

    r = client.put(f"/tasks/{first}", json={"done": "yes"})
    check("PUT non-bool done status", r.status_code, 400)
    check("PUT non-bool done message", r.json(),
          {"error": "done must be true or false"})

    r = client.put(f"/tasks/{first}", json={"title": "  "})
    check("PUT blank title status", r.status_code, 400)

    # A rejected update must not have half-applied itself.
    check("PUT rejected leaves the row alone",
          client.get(f"/tasks/{first}").json(),
          {"id": first, "title": "Both", "done": False})

    r = client.put("/tasks/999999", json={"title": "nope"})
    check("PUT unknown id status", r.status_code, 404)
    check("PUT unknown id body", r.json(), {"error": "Task 999999 not found"})

    # --- delete -------------------------------------------------------------
    for task_id in created:
        r = client.delete(f"/tasks/{task_id}")
        check(f"DELETE {task_id} status", r.status_code, 204)
        check(f"DELETE {task_id} body is genuinely empty", r.content, b"")

    r = client.delete(f"/tasks/{first}")
    check("DELETE twice is a 404", r.status_code, 404)
    check("GET after DELETE is a 404", client.get(f"/tasks/{first}").status_code, 404)

    # --- the documented contract -------------------------------------------
    schema = client.get("/openapi.json").json()
    has_422 = any("422" in op.get("responses", {})
                  for path in schema["paths"].values() for op in path.values())
    check("OpenAPI documents no 422 (they are all 400s)", has_422, False)


def main() -> int:
    if len(sys.argv) > 1:
        import httpx
        base = sys.argv[1].rstrip("/")
        print(f"Verifying the API over HTTP at {base}")
        with httpx.Client(base_url=base, timeout=10) as client:
            run(client)
    else:
        from fastapi.testclient import TestClient
        import main as app_module
        print(f"Verifying in-process against "
              f"{type(app_module.repo).__name__}")
        with TestClient(app_module.app) as client:
            run(client)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("The API contract holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
