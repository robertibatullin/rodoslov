import random
import sqlite3

import pytest

from rodoslov.manager.helpers import generate_person_id


class FixedRng(random.SystemRandom):
    """Returns a scripted sequence instead of random bits."""

    def __init__(self, values):
        super().__init__()
        self._values = iter(values)

    def getrandbits(self, k):
        return next(self._values)


def test_id_format():
    pid = generate_person_id(random.SystemRandom())
    assert pid.startswith("P-") and pid[2:].isdigit()


def test_skips_ids_in_given_set():
    assert generate_person_id(FixedRng([1, 2]), pids={"P-1"}) == "P-2"


def test_skips_ids_in_database(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO people (id, source_file) VALUES ('P-100', 'x')")
    conn.commit()
    conn.close()
    assert generate_person_id(FixedRng([100, 101]), db_path=db_path) == "P-101"


def test_missing_database_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        generate_person_id(random.SystemRandom(), db_path=tmp_path / "nope.db")
