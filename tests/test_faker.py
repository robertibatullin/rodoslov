import sys

import pytest
import yaml

from rodoslov.faker import faker as fk
from rodoslov.faker.faker import GenealogyFaker, PersonRecord, generate_family_tree


def generate(**kwargs):
    kwargs.setdefault("seed", 1)
    kwargs.setdefault("cutoff_year", 2000)
    return yaml.safe_load(generate_family_tree(1900, **kwargs))


def shape(tree):
    """Tree content with random ids replaced by index, for reproducibility checks."""
    return sorted((p["born"], p["gender"], p.get("died")) for p in tree["people"].values())


# ── helpers ───────────────────────────────────────────────────

def test_path_hash_is_deterministic_and_separator_insensitive():
    assert fk.path_hash("a/b.yaml", 5) == fk.path_hash("A\\B.yaml", 5)
    assert fk.path_hash("a/b.yaml", 5) != fk.path_hash("a/b.yaml", 6)


def test_century():
    assert [GenealogyFaker._century(y) for y in (1, 100, 101, 1850, 1900, 1901)] == [1, 1, 2, 19, 19, 20]


def test_load_names_with_name_header(tmp_path):
    f = tmp_path / "n.csv"
    f.write_text("id,name\n1,Anna\n2, Bea \n3,\n", encoding="utf-8")
    assert GenealogyFaker._load_names(str(f)) == ["Anna", "Bea"]


def test_load_names_single_column_without_header(tmp_path):
    f = tmp_path / "n.csv"
    f.write_text("Anna\nBea\n", encoding="utf-8")
    assert GenealogyFaker._load_names(str(f)) == ["Anna", "Bea"]


def test_load_names_multi_column_requires_name_column(tmp_path):
    f = tmp_path / "n.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name"):
        GenealogyFaker._load_names(str(f))


def test_parse_name_fields():
    parse = GenealogyFaker._parse_name_fields
    assert parse({"name": "Head of the family,John Smith"}) == ("John", "Smith")
    assert parse({"name": "John"}) == ("John", None)
    assert parse({}) == (None, None)


def test_is_permanent_id():
    assert GenealogyFaker._is_permanent_id("P-123")
    assert not GenealogyFaker._is_permanent_id("temp1")
    assert not GenealogyFaker._is_permanent_id("P-abc")


def test_resolve_name_source(tmp_path):
    assert fk._resolve_name_source({}, "male") is None
    assert fk._resolve_name_source({"male": "m.csv"}, "male") == "m.csv"
    resolved = fk._resolve_name_source({"dir": str(tmp_path), "male": "m.csv"}, "male")
    assert resolved == str(tmp_path.resolve() / "m.csv")


# ── demography config ─────────────────────────────────────────

@pytest.fixture
def engine():
    return GenealogyFaker(1900, seed=1)


def test_config_value_scalar_map_and_clamping(engine):
    assert engine._demography_config_value("marriage_age_lo", 1850) == 18
    assert engine._demography_config_value("life_expectancy_hi", 1850) == 80
    assert engine._demography_config_value("life_expectancy_hi", 1500) == 75  # clamped low
    assert engine._demography_config_value("life_expectancy_hi", 2500) == 90  # clamped high


def test_death_hazard_curve(engine):
    person = PersonRecord(id="P-1", gender="M", born=1850, died=None, premature_death_propensity=0.0)
    base = engine._demography_config_value("premature_death_probability", 1850)
    assert engine._death_hazard(person, 10) == pytest.approx(base)
    assert engine._death_hazard(person, 80) == 1.0
    middle = engine._death_hazard(person, 67)  # between 55 and 80
    assert base < middle < 1.0


def test_scaled_probability_is_clamped(engine):
    assert engine._scaled_probability("marriage_probability_per_year", 1900, 1.0) == pytest.approx(0.24)
    engine.demography_config["marriage_probability_per_year"] = 0.9
    assert engine._scaled_probability("marriage_probability_per_year", 1900, 1.0) == 1.0


def test_constructor_needs_year_or_extend():
    with pytest.raises(ValueError):
        GenealogyFaker()


# ── generation ────────────────────────────────────────────────

def test_generated_tree_is_consistent():
    tree = generate()
    people, families = tree["people"], tree["families"]
    assert people
    for p in people.values():
        assert p["born"] <= 2000
        assert p["gender"] in ("M", "F")
        assert p.get("died", p["born"]) >= p["born"]
        assert p.get("died", 0) <= 2000
    for fam in families:
        assert set(fam["parents"]) <= set(people)
        assert set(fam.get("children", [])) <= set(people)
        for cid in fam.get("children", []):
            assert people[cid]["born"] > fam["married"]


def test_ids_have_permanent_format():
    assert all(GenealogyFaker._is_permanent_id(pid) for pid in generate()["people"])


def test_same_seed_gives_same_tree_shape():
    assert shape(generate(seed=7)) == shape(generate(seed=7))


def test_progenitor_and_surname():
    tree = generate(surname="Doe", progenitor_gender="F")
    first = next(iter(tree["people"].values()))
    assert first["born"] == 1900 and first["gender"] == "F"
    assert first["name"] == "Doe"  # no name pools: surname only


def test_names_from_pools(tmp_path):
    m, f, s = (tmp_path / n for n in ("m.csv", "f.csv", "s.csv"))
    m.write_text("name\nAdam\n", encoding="utf-8")
    f.write_text("name\nEve\n", encoding="utf-8")
    s.write_text("name\nBrown\n", encoding="utf-8")
    tree = generate(surname="Doe", m_names=str(m), f_names=str(f), m_surnames=str(s), f_surnames=str(s))
    names = {p["name"] for p in tree["people"].values()}
    assert names <= {"Adam Doe", "Eve Doe", "Adam Brown", "Eve Brown"}


def test_majorat_marks_head_of_family():
    tree = generate(majorat=True)
    first = next(iter(tree["people"].values()))
    assert first["gender"] == "M"
    assert first["class_"] == ["head-of-family"]
    assert first["name"].startswith("Head of the family,")


def test_cutoff_excludes_later_births():
    tree = generate(cutoff_year=1950)
    assert all(p["born"] <= 1950 for p in tree["people"].values())


def test_retry_finds_living_person():
    tree = generate(retry_until_survives=True, max_attempts=200)
    assert any("died" not in p for p in tree["people"].values())


def test_retry_raises_when_no_attempts_allowed():
    with pytest.raises(RuntimeError):
        GenealogyFaker(1900, seed=1, cutoff_year=2000).generate_with_retry(max_attempts=0)


def test_custom_config_file(tmp_path):
    cfg = yaml.safe_load(fk.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["marriage_probability_per_year"] = 0.0
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")
    tree = generate(config_path=str(path))
    assert list(tree["people"]) and not tree["families"]


# ── extend mode ───────────────────────────────────────────────

def test_extend_fills_childless_marriage_and_assigns_ids(tmp_path):
    extend = tmp_path / "_extend.yaml"
    extend.write_text(
        yaml.dump({
            "people": {
                "P-111": {"name": "John Smith", "gender": "M", "born": 1950},
                "tmp_jane": {"name": "Jane Doe", "gender": "F", "born": 1952},
            },
            "families": [{"parents": ["P-111", "tmp_jane"], "married": 1975, "children": None}],
        }),
        encoding="utf-8",
    )
    tree = yaml.safe_load(
        generate_family_tree(seed=3, cutoff_year=2020, extend_path=str(extend))
    )
    assert "P-111" in tree["people"]  # permanent id kept
    assert "tmp_jane" not in tree["people"]  # placeholder replaced
    assert all(GenealogyFaker._is_permanent_id(pid) for pid in tree["people"])
    family = tree["families"][0]
    assert isinstance(family["children"], list)
    assert "P-111" in family["parents"] and "tmp_jane" not in family["parents"]
    for cid in family["children"]:
        assert tree["people"][cid]["born"] > 1975


def test_extend_leaves_existing_children_alone(tmp_path):
    extend = tmp_path / "e.yaml"
    extend.write_text(
        yaml.dump({
            "people": {
                "P-1": {"gender": "M", "born": 1950},
                "P-2": {"gender": "F", "born": 1952},
                "P-3": {"gender": "F", "born": 1980},
            },
            "families": [{"parents": ["P-1", "P-2"], "married": 1975, "children": ["P-3"]}],
        }),
        encoding="utf-8",
    )
    tree = yaml.safe_load(generate_family_tree(seed=3, cutoff_year=2020, extend_path=str(extend)))
    assert tree["families"][0]["children"] == ["P-3"]


# ── command line ──────────────────────────────────────────────

def test_main_writes_output_file(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "progenitor:\n  birth_year: 1900\n  gender: M\n  surname: Doe\nwrite_file: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["rodoslov-faker", str(tmp_path), "--seed", "1", "--cutoff", "1950"])
    fk.main()
    out = yaml.safe_load((tmp_path / "Doe.yaml").read_text(encoding="utf-8"))
    assert out["people"]


def test_main_without_config_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rodoslov-faker", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        fk.main()
    assert exc.value.code == 1
