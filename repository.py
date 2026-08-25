"""The storage contract the API depends on.

Everything above this line - routes, validation, error shapes - talks to a
TaskRepository and nothing else. It never learns whether the rows live in a
SQLite file, in Postgres, or anywhere else, which is the whole point: swapping
the database becomes a matter of writing one more class in this shape.

Two rules keep the contract honest, and every implementation has to hold them
up so the API behaves identically whichever one is plugged in:

1. Tasks come back as plain dicts of {"id": int, "title": str, "done": bool}.
   Real Python booleans, always - SQLite stores 0/1 and Postgres stores a true
   BOOLEAN, and hiding that difference is the repository's job, not a route's.
2. Ordering is by id, oldest first, and it is a promise rather than whatever
   the engine happens to do today.
"""

from abc import ABC, abstractmethod


class TaskRepository(ABC):
    """The five operations the task API needs from a database."""

    @abstractmethod
    def get_all(self) -> list[dict]:
        """Every task, oldest first. An empty table gives an empty list."""

    @abstractmethod
    def get_by_id(self, task_id: int) -> dict | None:
        """One task, or None when no row has that id."""

    @abstractmethod
    def create(self, title: str) -> dict:
        """Insert a task with the given title and done=False.

        The title arrives already validated and trimmed - checking it is the
        API's job, not the database's. The id is assigned by the database, and
        the stored row is what comes back.
        """

    @abstractmethod
    def update(self, task_id: int, fields: dict) -> dict | None:
        """Apply a partial update and return the row as it now stands.

        fields holds only the columns the client actually sent - "title", "done",
        or both, never an empty dict - so anything left out keeps its stored
        value. Returns None if the id does not exist.
        """

    @abstractmethod
    def remove(self, task_id: int) -> bool:
        """Delete a task. True if a row was deleted, False if there was none."""
