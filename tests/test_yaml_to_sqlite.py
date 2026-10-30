import sqlite3

import pytest

from rodoslov.manager.yaml_to_sqlite import yaml_to_sqlite, _family_key
from conftest import write_yaml


def rows(db, sql):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_builds_merged_tables(db_path):
    assert rows(db_path, "SELECT COUNT(*) FROM people") == [(6,)]
    assert rows(db_path, "SELECT COUNT(*) FROM families") == [(2,)]
    assert rows(db_path, "SELECT COUNT(*) FROM family_children") == [(2,)]


def test_person_in_two_files_lists_both_sources(db_path):
    (source,) = rows(db_path, "SELECT source_file FROM people WHERE id = 'P-3'")[0]
    assert "Smith" in source and "Doe" in source


def test_family_roles_assigned_by_gender(db_path):
    assert rows(db_path, "SELECT husband, wife FROM families WHERE married = 1974") == [("P-1", "P-2")]


def test_identity_collision_raises(community):
    write_yaml(
        community / "Doe" / "Doe.yaml",
        {"people": {"P-3": {"name": "Somebody Else", "gender": "F", "born": 1975}}},
    )
    with pytest.raises(ValueError, match="ID collision"):
        yaml_to_sqlite(community)


def test_duplicate_family_is_stored_once(community):
    write_yaml(
        community / "Doe" / "Doe.yaml",
        {
            "people": {
                "P-1": {"name": "John Smith", "gender": "M", "born": 1950, "died": 2020},
                "P-2": {"name": "Mary Jones", "gender": "F", "born": 1952},
                "P-3": {"name": "Ann Smith", "gender": "F", "born": 1975},
                "P-4": {"name": "Bob Smith", "gender": "M", "born": 1978},
            },
            "families": [{"parents": ["P-2", "P-1"], "married": 1974, "children": ["P-4", "P-3"]}],
        },
    )
    yaml_to_sqlite(community)
    assert rows(community / "genealogy.db", "SELECT COUNT(*) FROM families") == [(1,)]


def test_extend_files_are_skipped(community):
    write_yaml(
        community / "Smith" / "_extend.yaml",
        {"people": {"x": {"name": "Ghost", "gender": "M", "born": 1900}}},
    )
    yaml_to_sqlite(community)
    assert rows(community / "genealogy.db", "SELECT COUNT(*) FROM people WHERE id = 'x'") == [(0,)]


def test_biographies_survive_rebuild(db_path, community):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO biographies VALUES ('P-1', 'A farmer.')")
    conn.commit()
    conn.close()
    yaml_to_sqlite(community)
    assert rows(db_path, "SELECT bio FROM biographies WHERE id = 'P-1'") == [("A farmer.",)]


def test_orphan_biography_warns(db_path, community, capsys):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO biographies VALUES ('P-999', 'Nobody')")
    conn.commit()
    conn.close()
    yaml_to_sqlite(community)
    assert "P-999" in capsys.readouterr().out


def test_family_key_ignores_order():
    a = {"parents": ["a", "b"], "children": ["c", "d"], "married": 1900}
    b = {"parents": ["b", "a"], "children": ["d", "c"], "married": 1900}
    assert _family_key(a) == _family_key(b)
    assert _family_key(a) != _family_key({**a, "divorced": 1910})
