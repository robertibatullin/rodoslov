"""Shared fixtures: a tiny two-lineage community written to a temp directory."""

import pytest
import yaml

from rodoslov.manager.yaml_to_sqlite import yaml_to_sqlite


SMITH = {
    "people": {
        "P-1": {"name": "John Smith", "gender": "M", "born": 1950, "died": 2020},
        "P-2": {"name": "Mary Jones", "gender": "F", "born": 1952},
        "P-3": {"name": "Ann Smith", "gender": "F", "born": 1975},
        "P-4": {"name": "Bob Smith", "gender": "M", "born": 1978},
    },
    "families": [
        {"parents": ["P-1", "P-2"], "married": 1974, "children": ["P-3", "P-4"]},
    ],
}

DOE = {
    "people": {
        # P-3 also appears in Smith: same person, seen from the spouse's side.
        "P-3": {"name": "Ann Smith", "gender": "F", "born": 1975},
        "P-5": {"name": "Tom Doe", "gender": "M", "born": 1972},
        "P-6": {"name": "Eve White", "gender": "F", "born": 1990},
    },
    "families": [
        {"parents": ["P-3", "P-5"], "married": 2000, "divorced": 2010},
    ],
}


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def community(tmp_path):
    """Community directory with Smith/ and Doe/ lineages (no database yet)."""
    write_yaml(tmp_path / "Smith" / "Smith.yaml", SMITH)
    write_yaml(tmp_path / "Doe" / "Doe.yaml", DOE)
    return tmp_path


@pytest.fixture
def db_path(community):
    """Path to the genealogy.db built from the `community` fixture."""
    yaml_to_sqlite(community)
    return community / "genealogy.db"
