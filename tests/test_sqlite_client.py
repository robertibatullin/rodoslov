import pytest

from rodoslov.manager.sqlite_client import GenealogySQLiteClient


@pytest.fixture
def client(db_path):
    return GenealogySQLiteClient(db_path)


def test_find_living(client):
    assert set(client.find_living(1960)["id"]) == {"P-1", "P-2"}
    # P-1 died in 2020: alive in 2020, not in 2021
    assert "P-1" in set(client.find_living(2020)["id"])
    assert "P-1" not in set(client.find_living(2021)["id"])


def test_find_unmarried_excludes_married_includes_divorced(client):
    in_2005 = set(client.find_unmarried(2005, "F", min_age=18, max_age=40)["id"])
    assert "P-3" not in in_2005  # married 2000
    in_2015 = set(client.find_unmarried(2015, "F", min_age=18, max_age=45)["id"])
    assert in_2015 == {"P-3", "P-6"}  # Ann divorced in 2010


def test_find_unmarried_filters_gender(client):
    ids = set(client.find_unmarried(2015, "M", min_age=18, max_age=60)["id"])
    assert "P-3" not in ids and "P-6" not in ids


def test_find_surnames(client):
    df = client.find_surnames("Smith")
    assert set(df["id"]) == {"P-1", "P-3", "P-4"}
    assert set(client.find_surnames("Smith", "Doe")["id"]) == {"P-1", "P-3", "P-4", "P-5"}


def test_get_profile_by_id(client):
    df = client.get_profile(id="P-3")
    by_relation = df.groupby("relation")["id"].apply(set).to_dict()
    assert by_relation["self"] == {"P-3"}
    assert by_relation["parent"] == {"P-1", "P-2"}
    assert by_relation["sibling"] == {"P-4"}
    assert by_relation["spouse"] == {"P-5"}
    assert "child" not in by_relation


def test_get_profile_by_name(client):
    df = client.get_profile(name="John Smith")
    assert set(df[df["relation"] == "child"]["id"]) == {"P-3", "P-4"}


def test_get_profile_is_safe_against_quotes(client):
    assert client.get_profile(name="O'Brien\" OR 1=1 --").empty


@pytest.mark.parametrize("kwargs", [{}, {"foo": "x"}, {"id": "P-1", "name": "x"}])
def test_get_profile_requires_single_id_or_name(client, kwargs):
    with pytest.raises(ValueError):
        client.get_profile(**kwargs)


def test_write_bio_inserts_and_overwrites(client):
    client.write_bio("P-1", "  First version  ")
    assert client.get_profile(id="P-1").iloc[0]["bio"] == "First version"
    client.write_bio("P-1", "Second version")
    assert client.get_profile(id="P-1").iloc[0]["bio"] == "Second version"


def test_write_bio_unknown_person(client):
    with pytest.raises(ValueError, match="No person"):
        client.write_bio("P-404", "text")
