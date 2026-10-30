from pathlib import Path
import random

from rodoslov.manager.sqlite_client import GenealogySQLiteClient


def generate_person_id(
        id_rng: random.SystemRandom,
        db_path: str | Path | None = None,
        pids: set[str] | None = None
    ) -> str:
    """A fresh "P-<number>" id, unique within this run.
    Checks for duplicates across the given db and the additional set of pids (if any).

    Args:
        id_rng: A random.SystemRandom instance for generating unique IDs.
        db_path: Path to the SQLite database file.
        pids: A set of existing person IDs to avoid duplicates.
    """
    pids = pids or set()
    if db_path is not None:
        db_path = Path(db_path)
        if not db_path.exists():
            raise ValueError(f"Database file {db_path} does not exist")
        client = GenealogySQLiteClient(db_path)
        pids |= set(client.query("SELECT DISTINCT id FROM people")["id"])
    while True:
        pid = f'P-{id_rng.getrandbits(32)}'
        if pid not in pids:
            pids.add(pid)
            return pid
