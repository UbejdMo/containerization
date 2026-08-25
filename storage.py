"""Picks which TaskRepository the app runs against.

This is the one and only place that names a concrete database class. main.py
asks for "a repository" and gets one; adding or changing a backend happens here
and nowhere else, so no route or validation rule ever has to be edited to move
the data somewhere new.
"""

from repository import TaskRepository
from sqlite_repository import SQLiteTaskRepository


def get_repository() -> TaskRepository:
    """Build the repository the API should use."""
    return SQLiteTaskRepository()
