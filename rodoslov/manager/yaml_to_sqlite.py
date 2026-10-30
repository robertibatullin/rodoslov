import sqlite3
from pathlib import Path

import yaml

from sqlite_client import SQLITE_DB_FILENAME


# A person appearing in several files must agree on these, or two different
# people have been given the same id.
IDENTITY_FIELDS = ("name", "gender", "born")


# ── Conversion function ───────────────────────────────────────
def _family_key(fam: dict) -> tuple:
    """Identity of a family for dedup purposes.

    Families have no id in the YAML, so the same marriage can be transcribed
    independently in more than one source file (e.g. once from each
    spouse's own family tree). Two entries are the same family if they
    share both spouses, the same set of children, and the same marriage/
    divorce years.
    """
    parents = tuple(sorted(fam.get("parents") or []))
    children = tuple(sorted(fam.get("children") or []))
    return (parents, children, fam.get("married"), fam.get("divorced"))


def yaml_to_sqlite(community_dir: str | Path) -> None:
    """Recursively scan *.yaml / *.yml and load genealogical data into SQLite."""
    root = Path(community_dir).resolve()
    conn = sqlite3.connect(str(root / SQLITE_DB_FILENAME))
    cur = conn.cursor()
    # "biographies" is hand-written content, not derived from YAML, so it is
    # never dropped: only the YAML-derived tables are rebuilt.
    cur.executescript("""
        DROP TABLE IF EXISTS family_children;
        DROP TABLE IF EXISTS families;
        DROP TABLE IF EXISTS people;
        CREATE TABLE IF NOT EXISTS biographies (
            id   TEXT PRIMARY KEY,
            bio  VARCHAR
        );
        CREATE TABLE people (
            id           TEXT PRIMARY KEY,
            source_file  TEXT NOT NULL,
            name         TEXT,
            gender       TEXT,
            born         INTEGER,
            died         INTEGER
        );
        CREATE TABLE families (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file  TEXT NOT NULL,
            husband      TEXT REFERENCES people(id),
            wife         TEXT REFERENCES people(id),
            married      INTEGER,
            divorced     INTEGER
        );
        CREATE TABLE family_children (
            family_id  INTEGER NOT NULL REFERENCES families(id),
            person_id  TEXT NOT NULL REFERENCES people(id),
            PRIMARY KEY (family_id, person_id)
        );
    """)

    yaml_files = sorted(p for p in root.rglob("*.yaml") if not p.stem.endswith("_extend")) \
               + sorted(p for p in root.rglob("*.yml")   if not p.stem.endswith("_extend"))

    # Phase 1: union all YAML data
    all_people: dict[str, dict] = {}
    all_families: list[dict] = []
    seen_family_keys: set[tuple] = set()

    for ypath in yaml_files:
        rel = str(ypath.relative_to(root))
        data = yaml.safe_load(ypath.read_text(encoding="utf-8"))
        if not data:
            continue
        for pid, attrs in (data.get("people") or {}).items():
            if pid not in all_people:
                all_people[pid] = {**attrs, "_sources": [rel]}
                continue
            known = all_people[pid]
            if any(known.get(k) != attrs.get(k) for k in IDENTITY_FIELDS):
                raise ValueError(
                    f"ID collision: {pid} is {known.get('name')} ({known.get('born')}) "
                    f"in {', '.join(known['_sources'])}, but {attrs.get('name')} "
                    f"({attrs.get('born')}) in {rel}"
                )
            if rel not in known["_sources"]:
                known["_sources"].append(rel)
        for fam in data.get("families") or []:
            key = _family_key(fam)
            if key in seen_family_keys:
                continue
            seen_family_keys.add(key)
            all_families.append({**fam, "_source": rel})

    # Phase 2: write to SQLite
    for pid, attrs in all_people.items():
        cur.execute(
            "INSERT INTO people (id, source_file, name, gender, born, died) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, ", ".join(attrs["_sources"]), attrs.get("name"), attrs.get("gender"),
             attrs.get("born"), attrs.get("died")),
        )

    for fam in all_families:
        husband_id = wife_id = None
        for pid in fam.get("parents") or []:
            if pid in all_people:
                if all_people[pid].get("gender") == "M":
                    husband_id = pid
                else:
                    wife_id = pid
        cur.execute(
            "INSERT INTO families (source_file, husband, wife, married, divorced) "
            "VALUES (?, ?, ?, ?, ?)",
            (fam["_source"], husband_id, wife_id, fam.get("married"), fam.get("divorced")),
        )
        fam_db_id = cur.lastrowid
        for cid in fam.get("children") or []:
            if cid in all_people:
                cur.execute("INSERT INTO family_children VALUES (?, ?)",
                            (fam_db_id, cid))

    orphans = [r[0] for r in cur.execute(
        "SELECT id FROM biographies WHERE id NOT IN (SELECT id FROM people)")]
    if orphans:
        print(f"WARNING: biographies for ids no longer in people: {', '.join(orphans)}")

    conn.commit()
    conn.close()
    size_kb = (root / SQLITE_DB_FILENAME).stat().st_size / 1024
    print(f"Processed {len(yaml_files)} YAML file(s)")
    print(f"  -> {len(all_people)} people, {len(all_families)} families")
    print(f"  -> {root / SQLITE_DB_FILENAME}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python yaml_to_sqlite.py <community_dir>")
        sys.exit(1)
    community_dir = sys.argv[1]
    yaml_to_sqlite(community_dir)
