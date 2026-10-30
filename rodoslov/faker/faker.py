"""faker.py - Generate fake family trees in nissaga-compatible YAML format.

Each person's life is simulated year by year (birth to death or cutoff),
rolling a probability each year for death, marriage, and (while married)
childbirth.

Usage:
    python faker.py <lineage_dir> [--config FILE] [--seed SEED] [--cutoff YEAR]

    Arguments:
        lineage_dir  Directory holding this lineage's config.yaml (see
                     example/Smith/config.yaml). The output YAML is written into this same directory.

    Options:
        --config FILE   Path to the demography config YAML shared across
                         families - death/marriage/fertility rates etc.
                         (default: default_config.yaml next to this script)
        --seed SEED     Random seed for reproducibility (default: None)
        --cutoff YEAR   Stop generating beyond this year (default: current year)

Examples:
    python faker.py example/Smith
    python faker.py example/Smith --seed 99
    python faker.py example/Smith --config custom_config.yaml --cutoff 1980

"""

import argparse
import csv
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

DEFAULT_CUTOFF_YEAR = datetime.now().year
DEFAULT_CONFIG_PATH = Path(__file__).parent / "default_config.yaml"
_ID_RNG = random.SystemRandom()


def path_hash(file_path: str, seed: int = 0, modulus: int = 2**31 - 1) -> int:
    """Map a file path to a deterministic integer."""
    normalized = str(file_path).replace("\\", "/").strip().casefold()

    value = seed
    for character in normalized:
        value = (value * 131 + ord(character)) % modulus

    return value


@dataclass
class PersonRecord:
    id: str
    gender: str
    born: int
    died: int | None  # None if still alive
    first_name: str | None = None  # bare given name, for dedup and display; excludes surname
    surname: str | None = None
    marriage_propensity: float = 0.0
    divorce_propensity: float = 0.0
    premature_death_propensity: float = 0.0


class GenealogyFaker:
    """Generates nissaga-compatible YAML for a fake family tree."""

    def __init__(
        self,
        progenitor_birth_year: int | None = None,
        seed: int | None = None,
        cutoff_year: int | None = None,
        majorat: bool = False,
        demography_config_path: str | None = None,
        progenitor_gender: str | None = None,
        surname: str | None = None,
        extend_path: str | None = None,
        m_names: str | None = None,
        f_names: str | None = None,
        m_surnames: str | None = None,
        f_surnames: str | None = None,
    ):
        if extend_path is None and progenitor_birth_year is None:
            raise ValueError("Either progenitor_birth_year or extend_path must be given")
        self.progenitor_birth_year = progenitor_birth_year
        self.extend_path = extend_path
        self.cutoff_year = cutoff_year if cutoff_year is not None else DEFAULT_CUTOFF_YEAR
        seed_to_hash = seed if seed is not None else random.randint(0, 2**32 - 1)
        self.seed = path_hash((extend_path or ""), seed_to_hash)
        self.majorat = majorat
        self.progenitor_gender = progenitor_gender
        self.surname = surname
        with open(demography_config_path or DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
            self.demography_config = yaml.safe_load(f)
        self.m_names = self._load_names(m_names) if m_names else None
        self.f_names = self._load_names(f_names) if f_names else None
        self.m_surnames = self._load_names(m_surnames) if m_surnames else None
        self.f_surnames = self._load_names(f_surnames) if f_surnames else None
        self.person_counter = 0
        self.people: dict[str, dict] = {}
        self.families: list[dict] = []
        self.person_records: dict[str, PersonRecord] = {}

    def _gen_id(self) -> str:
        """A fresh "P-<number>" id, unique within this run.

        Drawn from an OS-entropy RNG rather than the seeded module-level one:
        ids must stay unique across separate runs that reuse the same seed,
        otherwise two families generated with one seed get identical ids
        and silently overwrite each other in the database.
        """
        # TODO: move this function out to manager; check duplicates across db, not just this run
        while True:
            pid = f'P-{_ID_RNG.getrandbits(32)}'
            if pid not in self.people:
                return pid

    @staticmethod
    def _load_names(path: str) -> list[str]:
        """Load a name list from a CSV file.

        Uses the "name" column if the header has one. Otherwise, if the file
        has a single column with no "name" header, uses every value in it
        (there's no header row to skip).
        """
        with open(path, newline="", encoding="utf-8") as f:
            rows = [row for row in csv.reader(f) if row]
        if not rows:
            return []
        header = rows[0]
        if len(header) > 1:
            try:
                idx = [h.strip().lower() for h in header].index("name")
            except ValueError:
                raise ValueError(f"{path}: multiple columns but none named 'name'")
            return [row[idx].strip() for row in rows[1:] if len(row) > idx and row[idx].strip()]
        if header[0].strip().lower() == "name":
            return [row[0].strip() for row in rows[1:] if row[0].strip()]
        return [row[0].strip() for row in rows if row[0].strip()]

    def _pick_name(self, gender: str, exclude: set[str] = frozenset()) -> str | None:
        """Pick a random name for `gender` from the configured pool, avoiding `exclude` if possible."""
        names = self.m_names if gender == "M" else self.f_names
        if not names:
            return None
        candidates = [n for n in names if n not in exclude] or names
        return random.choice(candidates)

    def _pick_surname(self, gender: str) -> str | None:
        """Pick a random surname for an unrelated spouse marrying into the family.

        Unlike first names, no dedup against siblings is needed - each
        spouse is a one-off marrying in from outside the lineage.
        """
        surnames = self.m_surnames if gender == "M" else self.f_surnames
        if not surnames:
            return None
        return random.choice(surnames)

    @staticmethod
    def _century(year: int) -> int:
        return (year - 1) // 100 + 1

    def _demography_config_value(self, key: str, year: int):
        """Look up a config value for the given year.

        A config entry may be a constant scalar, or a {century: value} map for
        values that vary over time (century = ordinal century of `year`, e.g.
        1850 -> 19). A year outside a map's range clamps to the nearest
        available century. This is the single place that should read
        self.demography_config, so any key can be made century-dependent purely by
        editing the YAML, with no code changes.
        """
        value = self.demography_config[key]
        if isinstance(value, dict):
            centuries = sorted(value)
            century = min(max(self._century(year), centuries[0]), centuries[-1])
            return value[century]
        return value

    def _scaled_probability(self, key: str, year: int, propensity: float) -> float:
        """A config probability individually scaled by a person's propensity.

        `propensity` is drawn per-person in (0,1); the effective probability
        is baseline * (1 + propensity), clamped to a valid [0,1] range.
        """
        return min(1.0, self._demography_config_value(key, year) * (1 + propensity))

    def _death_hazard(self, person: PersonRecord, age: int) -> float:
        """Probability that `person` dies this year, at this age.

        Small and constant before life_expectancy_lo, then grows linearly to
        a certainty (1.0) at life_expectancy_hi. The premature-death floor is
        individually scaled by the person's own premature_death_propensity.
        """
        life_expectancy_lo = self._demography_config_value("life_expectancy_lo", person.born)
        life_expectancy_hi = self._demography_config_value("life_expectancy_hi", person.born)
        premature_probability = self._scaled_probability(
            "premature_death_probability", person.born, person.premature_death_propensity
        )
        if age < life_expectancy_lo:
            return premature_probability
        if age >= life_expectancy_hi:
            return 1.0
        span = life_expectancy_hi - life_expectancy_lo
        return premature_probability + (age - life_expectancy_lo) / span * (1.0 - premature_probability)

    def _childbirth_probability(self, mother_birth_year: int) -> float:
        """Constant (age-independent) probability of a childbirth this year, while married."""
        fertility_rate = self._demography_config_value("fertility_rate", mother_birth_year)
        childbearing_age_lo = self._demography_config_value("childbearing_age_lo", mother_birth_year)
        childbearing_age_hi = self._demography_config_value("childbearing_age_hi", mother_birth_year)
        return fertility_rate / (childbearing_age_hi - childbearing_age_lo)

    def _try_childbirth(
        self,
        year: int,
        mother: PersonRecord,
        father: PersonRecord,
        family: dict,
        last_birth_year: int | None,
        used_child_names: set[str],
    ) -> tuple[PersonRecord | None, int | None]:
        """Roll a possible childbirth in `year` for a married couple.

        On a birth, appends the child's id to `family["children"]`, records
        the name in `used_child_names` for sibling dedup, and returns the
        child alongside the updated last-birth year; otherwise returns
        (None, last_birth_year) unchanged. The child inherits the father's
        surname.
        """
        mother_age = year - mother.born
        childbearing_age_lo = self._demography_config_value("childbearing_age_lo", mother.born)
        childbearing_age_hi = self._demography_config_value("childbearing_age_hi", mother.born)
        birth_interval_lo = self._demography_config_value("birth_interval_lo", mother.born)
        cooldown_ok = last_birth_year is None or (year - last_birth_year) >= birth_interval_lo
        if not (
            cooldown_ok
            and childbearing_age_lo <= mother_age <= childbearing_age_hi
            and random.random() < self._childbirth_probability(mother.born)
        ):
            return None, last_birth_year
        child_gender = "M" if random.random() < self._demography_config_value("gender_ratio", year) else "F"
        child = self._make_person(
            year, child_gender, exclude_names=used_child_names, surname=father.surname or self.surname
        )
        if child is None:
            return None, last_birth_year
        family.setdefault("children", []).append(child.id)
        if child.first_name is not None:
            used_child_names.add(child.first_name)
        return child, year

    def _make_person(
        self, birth_year: int, gender: str, exclude_names: set[str] = frozenset(),
        surname: str | None = None,
    ) -> PersonRecord | None:
        """Create a person. Returns None if birth_year > cutoff year (nonexistent).

        If a name pool is configured for `gender`, assigns a random name not
        in `exclude_names` (used to keep siblings from sharing a name).
        `name` is "Firstname Surname" (either half omitted if not
        available); nissaga renders whatever follows the last comma as the
        bold title and whatever precedes it as a smaller subtitle, blank
        when there's no comma at all — which is the case here, so this
        renders as a plain title with no subtitle (see
        `_mark_head_of_family` for the one case that adds a subtitle). Death
        is not decided here — it's resolved later, year by year, by
        whichever life simulation (tracked or death-only) owns this person.
        """
        if birth_year > self.cutoff_year:
            return None
        pid = self._gen_id()
        self.person_counter += 1
        person_data = {"born": birth_year, "gender": gender}
        name = self._pick_name(gender, exclude_names)
        display_name = " ".join(part for part in (name, surname) if part)
        if display_name:
            person_data["name"] = display_name
        self.people[pid] = person_data
        return PersonRecord(
            id=pid, gender=gender, born=birth_year, died=None, first_name=name, surname=surname,
            marriage_propensity=random.random(),
            divorce_propensity=random.random(),
            premature_death_propensity=random.random(),
        )

    def _make_spouse(
        self, marriage_year: int, person_age: int, gender: str, age_lo: int, age_hi: int
    ) -> PersonRecord | None:
        """Create an unrelated spouse for a marriage happening in `marriage_year`.

        The spouse's age is drawn near `person_age` (offset -3..+3 years, for
        any marriage, first or later) but clamped to [age_lo, age_hi] so the
        spouse is always within the marriageable age band themselves, not
        just the person they're marrying.
        """
        offset = random.randint(-3, 3)
        spouse_age = min(max(person_age + offset, age_lo), age_hi)
        return self._make_person(
            marriage_year - spouse_age, gender, surname=self._pick_surname(gender)
        )

    def _record_death(self, person: PersonRecord, year: int):
        person.died = year
        self.people[person.id]["died"] = year

    def _mark_head_of_family(self, person: PersonRecord):
        """Tag a person as head of the family.

        Sets nissaga's style-class field (its Config.fields alias to "class"
        isn't honored under pydantic v2, so the accepted key is "class_") for
        any custom styling hook, and also sets `name` to add "Head of the
        family" as a subtitle above the person's usual "Firstname Surname"
        title — nissaga's renderer draws whatever follows the last comma as
        the bold title and whatever precedes it as the subtitle, so the
        surname stays intact instead of being displaced by the label.
        """
        data = self.people[person.id]
        data["class_"] = ["head-of-family"]
        display_name = " ".join(part for part in (person.first_name, person.surname) if part) or person.id
        data["name"] = f"Head of the family,{display_name}"

    def _live_death_only(self, person: PersonRecord, start_year: int):
        """Roll a person's remaining lifespan with no marriage/childbirth events.

        Used for non-inheriting children under majorat, and for a widowed
        spouse who outlives the tracked-lineage member they married (their
        own future has no further consequences for the tree, but they still
        need an honest death year for output).
        """
        for year in range(start_year, self.cutoff_year + 1):
            age = year - person.born
            if random.random() < self._death_hazard(person, age):
                self._record_death(person, year)
                return

    def _live_lineage_member(self, person: PersonRecord):
        """Simulate a tracked-lineage member's life year by year.

        Each year: check death (own, then current spouse's, if married);
        check divorce, if married and still alive; roll a marriage if
        currently unmarried and within the marriageable age band;
        roll a childbirth if married and the mother
        is within the childbearing band. Recurses into children (respecting
        majorat) once this member's own life is fully resolved.
        """
        marriage_age_lo = self._demography_config_value("marriage_age_lo", person.born)
        marriage_age_hi = self._demography_config_value("marriage_age_hi", person.born)

        spouse: PersonRecord | None = None
        current_family: dict | None = None
        last_birth_year: int | None = None
        all_children: list[PersonRecord] = []
        used_child_names: set[str] = set()

        for year in range(person.born, self.cutoff_year + 1):
            age = year - person.born

            if random.random() < self._death_hazard(person, age):
                self._record_death(person, year)
                break

            if spouse is not None:
                spouse_age = year - spouse.born
                if random.random() < self._death_hazard(spouse, spouse_age):
                    self._record_death(spouse, year)
                    spouse = None
                    current_family = None
                    last_birth_year = None
                elif (
                    year > current_family["married"]
                    and random.random() < self._scaled_probability(
                        "divorce_probability_per_year", person.born, person.divorce_propensity
                    )
                ):
                    # Divorce ends the marriage just like a spouse's death
                    # would, freeing `person` to remarry — but the ex-spouse
                    # is alive, so their remaining life still needs resolving.
                    current_family["divorced"] = year
                    self._live_death_only(spouse, year + 1)
                    spouse = None
                    current_family = None
                    last_birth_year = None

            if (
                spouse is None
                and marriage_age_lo <= age <= marriage_age_hi
                and random.random() < self._scaled_probability(
                    "marriage_probability_per_year", person.born, person.marriage_propensity
                )
            ):
                spouse_gender = "F" if person.gender == "M" else "M"
                spouse = self._make_spouse(year, age, spouse_gender, marriage_age_lo, marriage_age_hi)
                current_family = {"parents": [person.id, spouse.id], "married": year}
                self.families.append(current_family)
                last_birth_year = None

            # Childbirth requires at least a year of marriage first (no
            # same-year wedding-and-birth)
            if spouse is not None and year > current_family["married"]:
                mother = person if person.gender == "F" else spouse
                father = person if person.gender == "M" else spouse
                child, last_birth_year = self._try_childbirth(
                    year, mother, father, current_family, last_birth_year, used_child_names
                )
                if child is not None:
                    all_children.append(child)

        # If this member died first, the surviving spouse's remaining
        # lifespan is still unresolved — settle it (no more events for them).
        if person.died is not None and spouse is not None:
            self._live_death_only(spouse, person.died + 1)

        # Recurse: each child may start their own family.
        # Under majorat, only the eldest son's line is carried forward —
        # younger children are recorded but their descendants are not generated.
        if self.majorat:
            elder_son = next((c for c in all_children if c.gender == "M"), None)
            for child in all_children:
                if child is elder_son:
                    self._mark_head_of_family(child)
                    self._live_lineage_member(child)
                else:
                    self._live_death_only(child, child.born)
        else:
            for child in all_children:
                self._live_lineage_member(child)

    @staticmethod
    def _parse_name_fields(person_data: dict) -> tuple[str | None, str | None]:
        """Recover (first_name, surname) for a person loaded from an `extend` file.

        Mirrors `_make_person`'s "Firstname Surname" encoding: whatever
        precedes the last comma (e.g. "Head of the family", from
        `_mark_head_of_family`) is a role marker, not part of the name, so
        it's dropped. The surname is then recovered as the last
        space-separated word — this project's name pools are all single
        words, so that's exact in practice, though it can't tell a lone
        word that's a first name (no surname pool configured) apart from
        one that's a surname (no first-name pool); it's treated as the
        former.
        """
        raw = person_data.get("name") or person_data.get("fullname")
        if not raw:
            return None, None
        if "," in raw:
            _, raw = raw.rsplit(",", 1)
        if " " in raw:
            first_name, surname = raw.rsplit(" ", 1)
            return first_name, surname
        return raw, None

    @staticmethod
    def _is_permanent_id(pid: str) -> bool:
        """True if `pid` is already a real database id ("P-<number>"), not a temp placeholder."""
        return pid.startswith("P-") and pid[2:].isdigit()

    def _load_extend(self, path: str):
        """Load an existing nissaga YAML to continue generating from.

        Populates `self.people`/`self.families` with the file's own data and
        `self.person_records` with a PersonRecord per loaded person, so
        `_extend_family` can reuse the same age/death/surname logic as fresh
        generation. Propensities aren't recoverable from the file and aren't
        needed here, since a loaded person's own life story is already fixed.

        A person already in the database carries a "P-<number>" id, which is
        kept unchanged. Anyone else (e.g. a hand-assigned "temp<n>"
        placeholder) is new and gets a real id from `_gen_id`, with every
        reference to them in `people`/`families` rewritten to match.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        people = data.get("people") or {}
        families = data.get("families") or []

        self.people = {pid: {} for pid in people if self._is_permanent_id(pid)}  # reserve for _gen_id
        id_map = {pid: (pid if self._is_permanent_id(pid) else self._gen_id()) for pid in people}
        self.people = {id_map[pid]: person_data for pid, person_data in people.items()}
        for family in families:
            family["parents"] = [id_map.get(pid, pid) for pid in family["parents"]]
            if family.get("children"):
                family["children"] = [id_map.get(cid, cid) for cid in family["children"]]
        self.families = families

        max_num = 0
        for pid, person_data in self.people.items():
            first_name, surname = self._parse_name_fields(person_data)
            self.person_records[pid] = PersonRecord(
                id=pid,
                gender=person_data["gender"],
                born=person_data["born"],
                died=person_data.get("died"),
                first_name=first_name,
                surname=surname,
            )
            digits = "".join(ch for ch in pid if ch.isdigit())
            if digits:
                max_num = max(max_num, int(digits))
        self.person_counter = max_num

    def _mother_father_ids(self, family: dict) -> tuple[str, str]:
        """Return (mother_id, father_id) for a family's two parents."""
        first_id, second_id = family["parents"]
        if self.person_records[first_id].gender == "M":
            return second_id, first_id
        return first_id, second_id

    def _extend_family(self, family: dict):
        """Generate children for a pre-existing, still-childless marriage.

        Mirrors the childbirth logic in `_live_lineage_member`, but the
        parents' own deaths and this marriage's divorce (if any) are already
        fixed by the loaded data rather than re-rolled — they only bound the
        window in which a child can still be born. Newly generated children
        then get the usual full lineage simulation (or majorat treatment).
        """
        mother_id, father_id = self._mother_father_ids(family)
        mother = self.person_records[mother_id]
        father = self.person_records[father_id]

        end_year = self.cutoff_year
        for boundary in (mother.died, father.died, family.get("divorced")):
            if boundary is not None:
                end_year = min(end_year, boundary)

        family["children"] = []

        last_birth_year: int | None = None
        used_child_names: set[str] = set()
        new_children: list[PersonRecord] = []
        for year in range(family["married"] + 1, end_year + 1):
            child, last_birth_year = self._try_childbirth(
                year, mother, father, family, last_birth_year, used_child_names
            )
            if child is not None:
                new_children.append(child)

        if self.majorat:
            elder_son = next((c for c in new_children if c.gender == "M"), None)
            for child in new_children:
                if child is elder_son:
                    self._mark_head_of_family(child)
                    self._live_lineage_member(child)
                else:
                    self._live_death_only(child, child.born)
        else:
            for child in new_children:
                self._live_lineage_member(child)

    def generate(self, seed: int | None = None) -> str:
        """Build the tree and return nissaga-compatible YAML.

        Uses `seed` if given, otherwise `self.seed`.
        """
        random.seed(seed if seed is not None else self.seed)
        self.person_counter = 0
        self.people.clear()
        self.families.clear()
        self.person_records.clear()

        if self.extend_path is not None:
            self._load_extend(self.extend_path)
            for family in list(self.families):
                if family.get("children") is None:
                    self._extend_family(family)
        else:
            if self.progenitor_gender is not None:
                progenitor_gender = self.progenitor_gender
            elif self.majorat:
                progenitor_gender = "M"
            else:
                progenitor_gender = "M" if random.random() < self._demography_config_value("gender_ratio", self.progenitor_birth_year) else "F"
            progenitor = self._make_person(self.progenitor_birth_year, progenitor_gender, surname=self.surname)
            if self.majorat:
                self._mark_head_of_family(progenitor)
            self._live_lineage_member(progenitor)

        nissaga_data = {
            "people": self.people,
            "families": self.families,
        }
        return yaml.dump(nissaga_data, default_flow_style=False, sort_keys=False)

    def generate_with_retry(self, max_attempts: int = 1000) -> str:
        """Retry with incrementing seeds (starting at self.seed) until the
        tree includes at least one person still alive at the cutoff year.

        Since the death hazard reaches certainty at life_expectancy_hi,
        nobody can be alive at the cutoff unless a descendant was actually
        born recently enough — so this alone also guarantees the lineage
        didn't die out early. Updates self.seed to the seed that succeeded.
        Raises RuntimeError if no attempt within max_attempts qualifies.
        """
        for attempt in range(max_attempts):
            seed = self.seed + attempt
            yaml_content = self.generate(seed=seed)
            if any("died" not in p for p in self.people.values()):
                self.seed = seed
                print(
                    f"Found a lineage surviving to the cutoff year at seed {seed} "
                    f"(attempt {attempt + 1}/{max_attempts})",
                    file=sys.stderr,
                )
                return yaml_content
        raise RuntimeError(
            f"No lineage surviving to the cutoff year found in {max_attempts} attempts "
            f"(seeds {self.seed}..{self.seed + max_attempts - 1})"
        )


def generate_family_tree(
    progenitor_birth_year: int | None = None,
    seed: int | None = None,
    cutoff_year: int | None = None,
    majorat: bool = False,
    config_path: str | None = None,
    progenitor_gender: str | None = None,
    surname: str | None = None,
    extend_path: str | None = None,
    retry_until_survives: bool = False,
    max_attempts: int = 1000,
    m_names: str | None = None,
    f_names: str | None = None,
    m_surnames: str | None = None,
    f_surnames: str | None = None,
) -> str:
    """Convenience wrapper around GenealogyFaker.

    If `retry_until_survives` is set, retries with incrementing seeds (see
    GenealogyFaker.generate_with_retry) instead of generating just once.
    """
    faker = GenealogyFaker(
        progenitor_birth_year,
        seed=seed,
        cutoff_year=cutoff_year,
        majorat=majorat,
        demography_config_path=config_path,
        progenitor_gender=progenitor_gender,
        surname=surname,
        extend_path=extend_path,
        m_names=m_names,
        f_names=f_names,
        m_surnames=m_surnames,
        f_surnames=f_surnames,
    )
    if retry_until_survives:
        return faker.generate_with_retry(max_attempts=max_attempts)
    return faker.generate()


def _resolve_name_source(name_sources: dict, key: str) -> str | None:
    """Join name_sources['dir'] with name_sources[key], if the latter is set."""
    filename = name_sources.get(key)
    if not filename:
        return None
    names_dir = name_sources.get("dir")
    return str(Path(names_dir) / filename) if names_dir else filename


def main():
    parser = argparse.ArgumentParser(
        description="Generate a fake family tree in nissaga-compatible YAML format."
    )
    parser.add_argument(
        "family_dir", type=str,
        help="Directory holding this family's config.yaml; output is written here"
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help=f"Path to the demography config YAML, shared across families "
             f"(default: {DEFAULT_CONFIG_PATH})"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed (default: None)"
    )
    parser.add_argument(
        "--cutoff", type=int, default=None,
        help=f"Stop generating beyond this year (default: {DEFAULT_CUTOFF_YEAR})"
    )
    args = parser.parse_args()

    family_dir = Path(args.family_dir)
    try:
        with open(family_dir / "config.yaml", "r", encoding="utf-8") as f:
            family_config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Config file not found: {family_dir / 'config.yaml'}", file=sys.stderr)
        sys.exit(1)

    extend_path = family_config.get("extend")
    progenitor = family_config.get("progenitor") or {}
    surname = progenitor.get("surname")
    name_sources = family_config.get("name_sources") or {}

    yaml_content = generate_family_tree(
        progenitor.get("birth_year"),
        seed=args.seed,
        cutoff_year=args.cutoff,
        majorat=family_config.get("majorat", False),
        config_path=args.config,
        progenitor_gender=progenitor.get("gender"),
        surname=surname,
        extend_path=extend_path,
        retry_until_survives=family_config.get("retry_until_survives", False),
        max_attempts=family_config.get("max-attempts", 1000),
        m_names=_resolve_name_source(name_sources, "male"),
        f_names=_resolve_name_source(name_sources, "female"),
        m_surnames=_resolve_name_source(name_sources, "male_surnames"),
        f_surnames=_resolve_name_source(name_sources, "female_surnames"),
    )

    if family_config.get("write_file", True):
        output_path = family_dir / family_config.get("output_path", f"{surname or family_dir.name}.yaml")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"Written to {output_path}", file=sys.stderr)
    else:
        print(yaml_content)


if __name__ == "__main__":
    main()
