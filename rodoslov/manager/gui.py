"""gui.py - Browser GUI for manually editing a genealogy YAML file.

Usage:
    rodoslov-gui lineage_file.yaml [--port 5000] [--no-browser]

Renders the family tree of the given nissaga-style YAML file (people +
families) in the browser, and lets you edit it with right-click menus:

    person box      Edit / Add spouse / Select spouse
    marriage box    Edit / Add child / Select child
    empty area      Add person

Every change is written to the YAML file and then yaml_to_sqlite.py is run so
that genealogy.db is always in sync (it is used to guarantee unique ids and to
offer existing people as spouses / children).
"""

import argparse
import random
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_file

from rodoslov.manager.sqlite_client import GenealogySQLiteClient
from rodoslov.manager.yaml_to_sqlite import yaml_to_sqlite, SQLITE_DB_FILENAME


BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "gui.html"

MIN_MARRIAGE_AGE = 18
SPOUSE_MAX_AGE_GAP = 10
_ID_RNG = random.SystemRandom()

app = Flask(__name__)
ALLOWED_HOSTS: set[str] = set()


class ApiError(Exception):
    """Invalid request; reported to the browser as a 400 JSON error."""


@app.errorhandler(ApiError)
def _handle_api_error(err: ApiError):
    return jsonify(error=str(err)), 400


@app.before_request
def _check_host():
    # Guards against DNS-rebinding: only answer requests addressed to localhost.
    if request.host not in ALLOWED_HOSTS:
        return jsonify(error="Forbidden host"), 403
    return None


# ── YAML / DB helpers ─────────────────────────────────────────

def load_data() -> dict:
    """Read the edited YAML file; always returns dict with "people" and "families" keys."""
    with open(YAML_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not data.get("people"):
        data["people"] = {}
    if not data.get("families"):
        data["families"] = []
    return data


def save_data(data: dict) -> None:
    """Atomically write `data` to the edited YAML file."""
    # Keep the file's existing line-ending style so edits don't rewrite every line.
    newline = "\r\n" if b"\r\n" in YAML_FILE.read_bytes() else "\n"
    tmp = YAML_FILE.with_suffix(YAML_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    tmp.replace(YAML_FILE)


def sync_db() -> str | None:
    """Rebuild genealogy.db from the YAML files; return a warning string on failure, else None."""
    try:
        yaml_to_sqlite(DB_PATH.parent)
    except Exception as e:
        return f"Saved, but database update failed: {e}"
    return None


def finish(data: dict, **extra):
    """Save the YAML, sync the database and build the success JSON response."""
    save_data(data)
    warning = sync_db()
    return jsonify(ok=True, warning=warning, **extra)


def db_person(pid: str) -> dict | None:
    """Look up a person in the database by id; None if absent."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, name, gender, born, died FROM people WHERE id = ?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "gender": row[2], "born": row[3], "died": row[4]}


def new_id(data: dict) -> str:
    """A fresh "P-<number>" id absent from both the YAML and the database."""
    conn = sqlite3.connect(DB_PATH)
    try:
        while True:
            pid = f"P-{_ID_RNG.getrandbits(32)}"
            if pid in data["people"]:
                continue
            if conn.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone() is None:
                return pid
    finally:
        conn.close()


def person_entry(name: str, gender: str, born: int, died: int | None) -> dict:
    """Build a YAML person dict, omitting empty name / death year."""
    entry = {"born": born, "gender": gender}
    if name:
        entry["name"] = name
    if died is not None:
        entry["died"] = died
    return entry


def import_person(data: dict, pid: str) -> None:
    """Make sure `pid` (an existing person from the DB) is present in this YAML file."""
    if pid in data["people"]:
        return
    row = db_person(pid)
    if row is None:
        raise ApiError(f"Unknown person {pid}")
    entry = person_entry(row["name"] or "", row["gender"], row["born"], row["died"])
    data["people"][pid] = entry


# ── Input parsing ─────────────────────────────────────────────

def body() -> dict:
    """The JSON object of the current request, or ApiError."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Expected a JSON body")
    return payload


def year_of(value, label: str, required: bool = False) -> int | None:
    """Parse a year (1..3000) from a request value; None if blank and not required."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ApiError(f"{label} is required")
        return None
    try:
        year = int(str(value).strip())
    except ValueError:
        raise ApiError(f"{label} must be a whole year")
    if not 1 <= year <= 3000:
        raise ApiError(f"{label} is out of range")
    return year


def text_of(value) -> str:
    """Stripped string of a request value (empty for None)."""
    return str(value or "").strip()


def gender_of(value) -> str:
    """Validate gender is "M" or "F"."""
    if value not in ("M", "F"):
        raise ApiError("Gender must be M or F")
    return value


def check_lifespan(born: int | None, died: int | None) -> None:
    """Raise ApiError if death precedes birth."""
    if born is not None and died is not None and died < born:
        raise ApiError("Death year is before birth year")


def get_person(data: dict, pid) -> dict:
    """The person dict for `pid`, or ApiError if unknown."""
    person = data["people"].get(pid)
    if person is None:
        raise ApiError(f"Unknown person {pid}")
    return person


def get_family(data: dict, index) -> dict:
    """The family at list position `index`, or ApiError if invalid."""
    try:
        return data["families"][int(index)]
    except (ValueError, TypeError, IndexError):
        raise ApiError("Unknown marriage")


def check_married_within(person: dict, married: int, label: str) -> None:
    """Raise ApiError if the marriage year is outside the person's lifespan."""
    born, died = person.get("born"), person.get("died")
    if born is not None and married < born:
        raise ApiError(f"Marriage is before the birth of {label}")
    if died is not None and married > died:
        raise ApiError(f"Marriage is after the death of {label}")


def df_records(df) -> list[dict]:
    """DataFrame to JSON-safe records (NaN -> None, born/died -> int)."""
    records = []
    for row in df.to_dict("records"):
        rec = {}
        for key, value in row.items():
            if value != value:  # NaN
                value = None
            elif key in ("born", "died") and value is not None:
                value = int(value)
            rec[key] = value
        records.append(rec)
    return records


# ── Routes ────────────────────────────────────────────────────

@app.get("/")
def index():
    return send_file(HTML_PATH)


@app.get("/api/tree")
def api_tree():
    data = load_data()
    people = {
        pid: {"id": pid, "name": p.get("name"), "gender": p.get("gender"),
              "born": p.get("born"), "died": p.get("died")}
        for pid, p in data["people"].items()
    }
    families = [
        {"index": i, "parents": f.get("parents") or [], "married": f.get("married"),
         "divorced": f.get("divorced"), "children": f.get("children") or []}
        for i, f in enumerate(data["families"])
    ]
    return jsonify(file=YAML_FILE.name, people=people, families=families)


@app.post("/api/person/edit")
def api_person_edit():
    req = body()
    data = load_data()
    person = get_person(data, req.get("id"))
    name = text_of(req.get("name"))
    born = year_of(req.get("born"), "Birth year", required=True)
    died = year_of(req.get("died"), "Death year")
    check_lifespan(born, died)
    person["born"] = born
    for key, value in (("name", name or None), ("died", died)):
        if value is None:
            person.pop(key, None)
        else:
            person[key] = value
    return finish(data)


@app.post("/api/person/add")
def api_person_add():
    req = body()
    data = load_data()
    born = year_of(req.get("born"), "Birth year", required=True)
    died = year_of(req.get("died"), "Death year")
    check_lifespan(born, died)
    pid = new_id(data)
    data["people"][pid] = person_entry(text_of(req.get("name")), gender_of(req.get("gender")), born, died)
    return finish(data, id=pid)


@app.get("/api/spouse/candidates")
def api_spouse_candidates():
    data = load_data()
    person = get_person(data, request.args.get("person_id"))
    year = year_of(request.args.get("year"), "Marriage year", required=True)
    check_married_within(person, year, "this person")
    gender = "F" if person.get("gender") == "M" else "M"
    max_age = year - person["born"] + SPOUSE_MAX_AGE_GAP
    df = GenealogySQLiteClient(DB_PATH).find_unmarried(
        year, gender, min_age=MIN_MARRIAGE_AGE, max_age=max_age
    )
    records = [r for r in df_records(df) if r["id"] != request.args.get("person_id")]
    return jsonify(candidates=records)


@app.post("/api/spouse/add")
def api_spouse_add():
    req = body()
    data = load_data()
    person_id = req.get("person_id")
    person = get_person(data, person_id)
    married = year_of(req.get("married"), "Marriage year", required=True)
    born = year_of(req.get("born"), "Birth year", required=True)
    died = year_of(req.get("died"), "Death year")
    check_lifespan(born, died)
    check_married_within(person, married, "this person")
    check_married_within({"born": born, "died": died}, married, "the spouse")
    gender = "F" if person.get("gender") == "M" else "M"
    spouse_id = new_id(data)
    data["people"][spouse_id] = person_entry(text_of(req.get("name")), gender, born, died)
    data["families"].append({"parents": [person_id, spouse_id], "married": married})
    return finish(data, id=spouse_id)


@app.post("/api/spouse/select")
def api_spouse_select():
    req = body()
    data = load_data()
    person_id = req.get("person_id")
    spouse_id = req.get("spouse_id")
    person = get_person(data, person_id)
    married = year_of(req.get("married"), "Marriage year", required=True)
    if not spouse_id or spouse_id == person_id:
        raise ApiError("Select a spouse")
    check_married_within(person, married, "this person")
    import_person(data, spouse_id)
    check_married_within(data["people"][spouse_id], married, "the spouse")
    data["families"].append({"parents": [person_id, spouse_id], "married": married})
    return finish(data)


@app.post("/api/family/edit")
def api_family_edit():
    req = body()
    data = load_data()
    family = get_family(data, req.get("index"))
    married = year_of(req.get("married"), "Marriage year", required=True)
    divorced = year_of(req.get("divorced"), "Divorce year")
    if divorced is not None and divorced < married:
        raise ApiError("Divorce is before the marriage")
    family["married"] = married
    if divorced is None:
        family.pop("divorced", None)
    else:
        family["divorced"] = divorced
    return finish(data)


@app.post("/api/child/add")
def api_child_add():
    req = body()
    data = load_data()
    family = get_family(data, req.get("index"))
    born = year_of(req.get("born"), "Birth year", required=True)
    died = year_of(req.get("died"), "Death year")
    check_lifespan(born, died)
    pid = new_id(data)
    data["people"][pid] = person_entry(text_of(req.get("name")), gender_of(req.get("gender")), born, died)
    family.setdefault("children", []).append(pid)
    return finish(data, id=pid)


@app.get("/api/child/candidates")
def api_child_candidates():
    data = load_data()
    family = get_family(data, request.args.get("index"))
    parents = family.get("parents") or []
    lower = family.get("married")
    deaths = [data["people"][p].get("died") for p in parents if p in data["people"]]
    deaths = [d for d in deaths if d is not None]
    upper = min(deaths) if deaths else None
    sql = """
        SELECT p.id, p.name, p.gender, p.born, p.died, p.source_file
        FROM people p
        WHERE p.id NOT IN (SELECT person_id FROM family_children)
          AND p.born IS NOT NULL
          AND (:lower IS NULL OR p.born > :lower)
          AND (:upper IS NULL OR p.born < :upper)
        ORDER BY p.born, p.name
    """
    df = GenealogySQLiteClient(DB_PATH).query(sql, {"lower": lower, "upper": upper})
    records = [r for r in df_records(df) if r["id"] not in parents]
    return jsonify(candidates=records)


@app.post("/api/child/select")
def api_child_select():
    req = body()
    data = load_data()
    family = get_family(data, req.get("index"))
    child_id = req.get("child_id")
    if not child_id or child_id in (family.get("parents") or []):
        raise ApiError("Select a child")
    if any(child_id in (f.get("children") or []) for f in data["families"]):
        raise ApiError("This person already has parents")
    import_person(data, child_id)
    family.setdefault("children", []).append(child_id)
    return finish(data)


# ── Entry point ───────────────────────────────────────────────

def main() -> None:
    """
    lineage path is a path to yaml file in a lineage subdir in a community dir
    """
    parser = argparse.ArgumentParser(description="Browser GUI for editing a genealogy YAML file.")
    parser.add_argument("lineage_path", type=str, help="genealogy YAML file to edit")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    lineage_path = Path(args.lineage_path).resolve()
    if not lineage_path.is_file():
        sys.exit(f"File not found: {lineage_path}")
    global DB_PATH
    community_dir = lineage_path.parent.parent
    DB_PATH = community_dir / SQLITE_DB_FILENAME
    global YAML_FILE
    YAML_FILE = lineage_path

    if not DB_PATH.exists():
        print("genealogy.db not found, building it...", file=sys.stderr)
        warning = sync_db()
        if warning:
            sys.exit(warning)

    ALLOWED_HOSTS.update({f"127.0.0.1:{args.port}", f"localhost:{args.port}"})
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"Editing {lineage_path} at {url}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
