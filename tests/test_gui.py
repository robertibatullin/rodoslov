import sqlite3
from pathlib import Path

import pytest
import yaml

from rodoslov.manager import gui
from rodoslov.manager.sqlite_client import GenealogySQLiteClient


@pytest.fixture
def client(community, db_path, monkeypatch):
    """Flask test client editing Smith.yaml of the temp community."""
    monkeypatch.setattr(gui, "YAML_FILE", community / "Smith" / "Smith.yaml", raising=False)
    monkeypatch.setattr(gui, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(gui, "ALLOWED_HOSTS", {"localhost"})
    return gui.app.test_client()


def saved(community):
    return yaml.safe_load((community / "Smith" / "Smith.yaml").read_text(encoding="utf-8"))


# ── parsing helpers ───────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [("1900", 1900), (" 1900 ", 1900), (1900, 1900), ("", None), (None, None)])
def test_year_of_valid(value, expected):
    assert gui.year_of(value, "Year") == expected


@pytest.mark.parametrize("value", ["abc", "0", "3001", "19.5"])
def test_year_of_invalid(value):
    with pytest.raises(gui.ApiError):
        gui.year_of(value, "Year")


def test_year_of_required():
    with pytest.raises(gui.ApiError, match="required"):
        gui.year_of("", "Year", required=True)


def test_gender_of():
    assert gui.gender_of("M") == "M"
    with pytest.raises(gui.ApiError):
        gui.gender_of("X")


def test_check_lifespan():
    gui.check_lifespan(1900, None)
    gui.check_lifespan(1900, 1900)
    with pytest.raises(gui.ApiError):
        gui.check_lifespan(1900, 1899)


def test_person_entry_omits_empty_fields():
    assert gui.person_entry("", "M", 1900, None) == {"born": 1900, "gender": "M"}
    assert gui.person_entry("X", "F", 1900, 1950) == {"born": 1900, "gender": "F", "name": "X", "died": 1950}


# ── security ──────────────────────────────────────────────────

def test_foreign_host_rejected(client):
    assert client.get("/api/tree", headers={"Host": "evil.example"}).status_code == 403


# ── read ──────────────────────────────────────────────────────

def test_index_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    response.close()


def test_tree(client):
    data = client.get("/api/tree").get_json()
    assert data["file"] == "Smith.yaml"
    assert set(data["people"]) == {"P-1", "P-2", "P-3", "P-4"}
    assert data["families"][0]["children"] == ["P-3", "P-4"]


# ── persons ───────────────────────────────────────────────────

def test_add_person_saves_yaml_and_syncs_db(client, community):
    response = client.post("/api/person/add", json={"name": "New Guy", "gender": "M", "born": "2000"})
    body = response.get_json()
    assert response.status_code == 200 and body["warning"] is None
    pid = body["id"]
    assert saved(community)["people"][pid] == {"born": 2000, "gender": "M", "name": "New Guy"}
    assert gui.db_person(pid)["name"] == "New Guy"


@pytest.mark.parametrize("payload", [
    {"gender": "X", "born": 2000},
    {"gender": "M"},
    {"gender": "M", "born": 2000, "died": 1999},
])
def test_add_person_validation(client, payload):
    response = client.post("/api/person/add", json=payload)
    assert response.status_code == 400 and "error" in response.get_json()


def test_non_json_body_rejected(client):
    assert client.post("/api/person/add", data="nope").status_code == 400


def test_edit_person(client, community):
    response = client.post("/api/person/edit", json={"id": "P-1", "name": "Johnny", "born": 1951, "died": ""})
    assert response.status_code == 200
    person = saved(community)["people"]["P-1"]
    assert person["name"] == "Johnny" and person["born"] == 1951 and "died" not in person


def test_edit_unknown_person(client):
    assert client.post("/api/person/edit", json={"id": "P-404", "born": 1900}).status_code == 400


# ── spouses ───────────────────────────────────────────────────

def test_add_spouse(client, community):
    response = client.post(
        "/api/spouse/add", json={"person_id": "P-4", "name": "Wife", "born": 1980, "married": 2005}
    )
    assert response.status_code == 200
    data = saved(community)
    spouse = data["people"][response.get_json()["id"]]
    assert spouse["gender"] == "F"  # opposite of P-4
    assert data["families"][-1]["married"] == 2005


def test_add_spouse_married_before_birth_rejected(client):
    response = client.post(
        "/api/spouse/add", json={"person_id": "P-4", "born": 1980, "married": 1970}
    )
    assert response.status_code == 400


def test_spouse_candidates(client):
    response = client.get("/api/spouse/candidates?person_id=P-4&year=2010")
    ids = {c["id"] for c in response.get_json()["candidates"]}
    assert "P-6" in ids  # unmarried woman from another lineage
    assert "P-3" in ids  # divorced in 2010
    assert "P-4" not in ids


def test_select_spouse_imports_person(client, community):
    response = client.post("/api/spouse/select", json={"person_id": "P-4", "spouse_id": "P-6", "married": 2012})
    assert response.status_code == 200
    data = saved(community)
    assert data["people"]["P-6"]["name"] == "Eve White"
    assert data["families"][-1] == {"parents": ["P-4", "P-6"], "married": 2012}


@pytest.mark.parametrize("spouse_id", [None, "P-4", "P-404"])
def test_select_spouse_invalid(client, spouse_id):
    response = client.post("/api/spouse/select", json={"person_id": "P-4", "spouse_id": spouse_id, "married": 2012})
    assert response.status_code == 400


# ── families ──────────────────────────────────────────────────

def test_edit_family_sets_and_clears_divorce(client, community):
    assert client.post("/api/family/edit", json={"index": 0, "married": 1974, "divorced": 1990}).status_code == 200
    assert saved(community)["families"][0]["divorced"] == 1990
    assert client.post("/api/family/edit", json={"index": 0, "married": 1974, "divorced": ""}).status_code == 200
    assert "divorced" not in saved(community)["families"][0]


def test_edit_family_divorce_before_marriage(client):
    assert client.post("/api/family/edit", json={"index": 0, "married": 1974, "divorced": 1970}).status_code == 400


def test_edit_unknown_family(client):
    assert client.post("/api/family/edit", json={"index": 9, "married": 1974}).status_code == 400


# ── children ──────────────────────────────────────────────────

def test_add_child(client, community):
    response = client.post("/api/child/add", json={"index": 0, "name": "Baby", "gender": "F", "born": 1985})
    assert response.status_code == 200
    data = saved(community)
    assert response.get_json()["id"] in data["families"][0]["children"]


def test_child_candidates_are_parentless_and_in_range(client):
    ids = {c["id"] for c in client.get("/api/child/candidates?index=0").get_json()["candidates"]}
    # P-6 (b. 1990) has no parents; P-1/P-2 are the parents; P-5 was born before the marriage.
    assert ids == {"P-6"}


def test_child_candidate_bounds_are_inclusive(client):
    for born in (1973, 1974, 2020, 2021):
        client.post("/api/person/add", json={"name": f"B{born}", "gender": "M", "born": born})
    candidates = client.get("/api/child/candidates?index=0").get_json()["candidates"]
    born_years = {c["born"] for c in candidates}
    # married 1974, father died 2020: 1974 and 2020 are allowed, 1973 and 2021 are not
    assert {1974, 2020} <= born_years and not {1973, 2021} & born_years


def test_child_candidates_stop_at_divorce(client):
    client.post("/api/family/edit", json={"index": 0, "married": 1974, "divorced": 1980})
    for born in (1980, 1981):
        client.post("/api/person/add", json={"name": f"B{born}", "gender": "F", "born": born})
    born_years = {c["born"] for c in client.get("/api/child/candidates?index=0").get_json()["candidates"]}
    assert 1980 in born_years and 1981 not in born_years and 1990 not in born_years


def test_select_child(client, community):
    response = client.post("/api/child/select", json={"index": 0, "child_id": "P-6"})
    assert response.status_code == 200
    assert "P-6" in saved(community)["families"][0]["children"]
    again = client.post("/api/child/select", json={"index": 0, "child_id": "P-6"})
    assert again.status_code == 400 and "already has parents" in again.get_json()["error"]


def test_select_child_cannot_be_own_parent(client):
    assert client.post("/api/child/select", json={"index": 0, "child_id": "P-1"}).status_code == 400


# ── tree info: biography and multi-file people ────────────────

def test_tree_has_bio_and_other_files(client, db_path):
    GenealogySQLiteClient(db_path).write_bio("P-1", "A farmer.")
    people = client.get("/api/tree").get_json()["people"]
    assert people["P-1"]["bio"] == "A farmer." and people["P-1"]["also_in"] == []
    assert people["P-2"]["bio"] is None
    assert people["P-3"]["also_in"] == [str(Path("Doe") / "Doe.yaml")]  # also listed in the Doe lineage


# ── delete person ─────────────────────────────────────────────

def db_rows(db_path, sql):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_delete_person_keeps_spouse_and_children(client, community, db_path):
    response = client.post("/api/person/delete", json={"id": "P-1"})
    assert response.status_code == 200 and response.get_json()["warning"] is None
    data = saved(community)
    assert "P-1" not in data["people"]
    assert data["families"][0]["parents"] == ["P-2"]
    assert data["families"][0]["children"] == ["P-3", "P-4"]
    assert db_rows(db_path, "SELECT id FROM people WHERE id = 'P-1'") == []
    assert db_rows(db_path, "SELECT husband, wife FROM families WHERE married = 1974") == [(None, "P-2")]


def test_delete_child_removes_it_from_children(client, community):
    client.post("/api/person/delete", json={"id": "P-4"})
    data = saved(community)
    assert "P-4" not in data["people"]
    assert data["families"][0]["children"] == ["P-3"]
    assert data["families"][0]["parents"] == ["P-1", "P-2"]


def test_delete_last_parent_drops_the_family(client, community):
    client.post("/api/person/delete", json={"id": "P-1"})
    client.post("/api/person/delete", json={"id": "P-2"})
    data = saved(community)
    assert data["families"] == []
    assert {"P-3", "P-4"} <= set(data["people"])


def test_delete_person_drops_biography(client, db_path):
    GenealogySQLiteClient(db_path).write_bio("P-1", "A farmer.")
    client.post("/api/person/delete", json={"id": "P-1"})
    assert db_rows(db_path, "SELECT id FROM biographies") == []


def test_delete_person_listed_in_another_yaml_stays_in_database(client, community, db_path):
    GenealogySQLiteClient(db_path).write_bio("P-3", "Ann's story.")
    client.post("/api/person/delete", json={"id": "P-3"})
    assert "P-3" not in saved(community)["people"]
    assert db_rows(db_path, "SELECT id FROM people WHERE id = 'P-3'") == [("P-3",)]  # still in Doe.yaml
    assert db_rows(db_path, "SELECT bio FROM biographies WHERE id = 'P-3'") == [("Ann's story.",)]


def test_delete_unknown_person(client):
    assert client.post("/api/person/delete", json={"id": "P-404"}).status_code == 400


def test_delete_spouse_of_childless_marriage_removes_marriage(client, community, db_path):
    client.post("/api/spouse/add", json={"person_id": "P-4", "name": "Wife", "born": 1980, "married": 2005})
    assert len(saved(community)["families"]) == 2
    wife = next(pid for pid, p in saved(community)["people"].items() if p.get("name") == "Wife")
    client.post("/api/person/delete", json={"id": wife})
    data = saved(community)
    assert len(data["families"]) == 1 and data["families"][0]["married"] == 1974
    assert "P-4" in data["people"]
    assert db_rows(db_path, "SELECT COUNT(*) FROM families") == [(2,)]  # 1 Smith + 1 Doe


# ── delete marriage ───────────────────────────────────────────

def test_delete_marriage_removes_father_only(client, community, db_path):
    response = client.post("/api/family/delete", json={"index": 0})
    assert response.status_code == 200
    data = saved(community)
    assert {"P-1", "P-2", "P-3", "P-4"} <= set(data["people"])  # nobody deleted
    assert data["families"][0]["parents"] == ["P-2"]
    assert data["families"][0]["children"] == ["P-3", "P-4"]
    assert db_rows(db_path, "SELECT husband, wife FROM families WHERE married = 1974") == [(None, "P-2")]


def test_father_less_marriage_is_removed_with_its_last_child(client, community):
    client.post("/api/family/delete", json={"index": 0})  # leaves mother + children
    client.post("/api/child/select", json={"index": 0, "child_id": "P-6"})
    assert client.post("/api/family/delete", json={"index": 0}).status_code == 400  # children keep it alive
    for child in ("P-3", "P-4", "P-6"):
        client.post("/api/person/delete", json={"id": child})
    assert saved(community)["families"] == []


def test_delete_childless_marriage_removes_entry(client, community):
    client.post("/api/spouse/add", json={"person_id": "P-4", "name": "Wife", "born": 1980, "married": 2005})
    assert client.post("/api/family/delete", json={"index": 1}).status_code == 200
    data = saved(community)
    assert len(data["families"]) == 1
    assert len(data["people"]) == 5  # nobody deleted


def test_delete_unknown_marriage(client):
    assert client.post("/api/family/delete", json={"index": 9}).status_code == 400


# ── db sync ───────────────────────────────────────────────────

def test_new_id_is_unique(client):
    data = gui.load_data()
    ids = {gui.new_id(data) for _ in range(20)}
    assert len(ids) == 20 and not ids & set(data["people"])


def test_sync_failure_is_reported_as_warning(client, monkeypatch):
    def boom(_):
        raise RuntimeError("disk full")
    monkeypatch.setattr(gui, "yaml_to_sqlite", boom)
    body = client.post("/api/person/add", json={"gender": "M", "born": 2000}).get_json()
    assert "disk full" in body["warning"]
