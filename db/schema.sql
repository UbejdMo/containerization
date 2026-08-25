-- The tasks table, and the three example rows a fresh database starts with.
--
-- This file is mounted into the Postgres container at
-- /docker-entrypoint-initdb.d/schema.sql, so the official image runs it once,
-- automatically, the first time the data volume is initialised. There is no
-- migration step to remember and no schema code inside the app.
--
-- Both statements are written to be safe to run more than once, so the file can
-- also be applied by hand against an existing database:
--     docker compose exec -T db psql -U tasks -d tasks -f /docker-entrypoint-initdb.d/schema.sql

CREATE TABLE IF NOT EXISTS tasks (
    -- The SQLite version used INTEGER PRIMARY KEY, which aliases rowid and hands
    -- out max(id) + 1. Postgres has no rowid; IDENTITY is its equivalent, backed
    -- by a sequence. The visible difference is that a sequence never reuses an
    -- id after a delete, where SQLite's rowid would. Both satisfy the API's only
    -- promise about ids - that the database assigns them and they are integers.
    id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT    NOT NULL,
    -- Postgres has a real BOOLEAN, so unlike SQLite there is no 0/1 to convert
    -- back on the way out.
    done  BOOLEAN NOT NULL DEFAULT FALSE
);

-- Seed the same three example tasks the SQLite version used, and only when the
-- table is genuinely empty. The WHERE NOT EXISTS guard is what stops a re-run
-- from stacking three more copies on top of real data.
INSERT INTO tasks (title, done)
SELECT seed.title, seed.done
FROM (VALUES
    ('Buy milk',                            FALSE),
    ('Read a chapter of the FastAPI docs',  TRUE),
    ('Walk the dog',                        FALSE)
) AS seed(title, done)
WHERE NOT EXISTS (SELECT 1 FROM tasks);
