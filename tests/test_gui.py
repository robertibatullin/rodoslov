import pytest
import yaml

from rodoslov.manager import gui


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


def test_select_child(client, community):
    response = client.post("/api/child/select", json={"index": 0, "child_id": "P-6"})
    assert response.status_code == 200
    assert "P-6" in saved(community)["families"][0]["children"]
    again = client.post("/api/child/select", json={"index": 0, "child_id": "P-6"})
    assert again.status_code == 400 and "already has parents" in again.get_json()["error"]


def test_select_child_cannot_be_own_parent(client):
    assert client.post("/api/child/select", json={"index": 0, "child_id": "P-1"}).status_code == 400


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
