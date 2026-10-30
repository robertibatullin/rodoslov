# rodoslov

Lightweight genealogy package. Family trees live in plain YAML files that can be edited by hand or through a browser GUI, and are synced to a SQLite database for querying. A fake-genealogy generator is included.

## Concepts

- **Lineage**: one YAML file (`people` + `families`) describing a progenitor's descendants and their spouses.
- **Community**: a directory of lineage subdirectories. The same person (same id) may appear in several lineages, e.g. as a child in one and a spouse in another.
- **Database**: `genealogy.db` in the community root, rebuilt from all YAML files. The `biographies` table is hand-written and preserved on rebuild.

```
my_community/
    Smith/Smith.yaml
    Doe/Doe.yaml
    genealogy.db
```

## Install

```
pip install git+<repo-url>
```

Requires Python 3.10+. For development: `pip install -e .[dev]`.

## YAML format

```yaml
people:
  P-1234:
    name: John Smith
    gender: M          # M or F
    born: 1980
    died: 2050         # optional
families:
- parents: [P-1234, P-5678]
  married: 2005
  divorced: 2010       # optional
  children: [P-9012]   # optional
```

## Commands

| Command | Purpose |
| --- | --- |
| `rodoslov-sync <community_dir>` | Rebuild `genealogy.db` from all YAML files (files named `*_extend.yaml` are skipped). |
| `rodoslov-gui <lineage.yaml> [--port 5000] [--no-browser]` | Browser GUI to edit one lineage file; every change is saved and the database re-synced. |
| `rodoslov-faker <lineage_dir> [--config FILE] [--seed N] [--cutoff YEAR]` | Generate a fictional lineage from `<lineage_dir>/config.yaml`. |

The faker's `config.yaml` either defines a `progenitor` (birth year, gender, surname) or an `extend` file to continue from. See `example_community/` for both variants. Demography (mortality, marriage, fertility) is configured in `rodoslov/faker/default_config.yaml`.

## Python API

```python
from rodoslov.manager.sqlite_client import GenealogySQLiteClient

db = GenealogySQLiteClient("example_community/genealogy.db")
db.find_living(1990)
db.find_unmarried(year=2000, gender="F", min_age=18, max_age=35)
db.find_surnames("Smith")
db.get_profile(name="John Smith")
db.write_bio("P-1234", "Biography text")
```

```python
from rodoslov.faker.faker import generate_family_tree

yaml_text = generate_family_tree(1900, seed=1, cutoff_year=2000, surname="Doe")
```

## Tests

```
pytest
```

## License

GPL-3.0, see [LICENSE](LICENSE).
