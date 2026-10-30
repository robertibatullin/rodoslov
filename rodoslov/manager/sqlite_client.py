import sqlite3
from pathlib import Path

import pandas as pd


SQLITE_DB_FILENAME = "genealogy.db"


class GenealogySQLiteClient:
    def __init__(self, db_path: str | Path):
        self.db_path = db_path

    def query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(sql, conn, params=params)
        conn.close()
        return df

    def find_living(self, year: int) -> pd.DataFrame:
        sql = """
            SELECT id, name, gender, born, died, source_file
            FROM people
            WHERE born <= :year AND (died IS NULL OR died >= :year)
        """
        return self.query(sql, {"year": year})

    def find_unmarried(
            self,
            year: int,
            gender: str,
            min_age: int,
            max_age: int
            ) -> pd.DataFrame: 
        min_birth_year = year - max_age
        max_birth_year = year - min_age
        sql = """
        SELECT p.id, p.name, p.born, p.died, p.source_file
        FROM   people p
        LEFT JOIN families f
               ON (f.husband = p.id OR f.wife = p.id)
              AND f.married <= :year
              AND (f.divorced IS NULL OR f.divorced > :year)
        WHERE  p.born >= :min_birth_year
          AND p.born <= :max_birth_year
          AND  (p.died IS NULL OR p.died >= :year)
          AND  f.id IS NULL
          AND p.gender = :gender
        ORDER  BY p.born
        """
        return self.query(sql, {
            "year": year,
            "gender": gender,
            "min_birth_year": min_birth_year,
            "max_birth_year": max_birth_year
        })

    def find_surnames(self, *surnames: str) -> pd.DataFrame:
        sql = f"""
        SELECT id, name, born, died, source_file
        FROM   people
        WHERE  {" OR ".join([f"name LIKE '%' || :surname{i}" for i in range(len(surnames))])}
        ORDER  BY name
        """
        params = {f"surname{i}": f"{surname}" for i, surname in enumerate(surnames)}
        return self.query(sql, params=params)

    def write_bio(self, id: str, bio: str) -> None:
        """
        Set the biography of the person with the given id (insert or overwrite).
        """
        conn = sqlite3.connect(self.db_path)
        try:
            if conn.execute("SELECT 1 FROM people WHERE id = ?", (id,)).fetchone() is None:
                raise ValueError(f"No person with id {id!r}.")
            conn.execute(
                "INSERT INTO biographies (id, bio) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET bio = excluded.bio",
                (id, bio.strip()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_profile(self, **kwargs) -> pd.DataFrame:
        """
        Get a profile by name or by id. The 'bio' column is filled for the 'self' row only.
        """
        kwarg_keys = list(kwargs.keys())
        if len(kwarg_keys) != 1 or kwarg_keys[0] not in ["name", "id"]:
            raise ValueError("You must provide exactly one of 'name' or 'id' as a keyword argument.")
        if "name" in kwargs:
            condition = f'p.name = {kwargs["name"]!r}'
        else:
            condition = f'p.id = {kwargs["id"]!r}'

        sql = f"""
        SELECT 'self'   AS relation, p.id, p.name, p.gender, p.born, p.died,
            NULL AS married, NULL AS divorced, p.source_file, b.bio AS bio
        FROM   people p
        LEFT JOIN biographies b ON b.id = p.id
        WHERE  {condition}

        UNION ALL

        SELECT 'parent', par.id, par.name, par.gender, par.born, par.died,
            NULL, NULL, par.source_file, NULL
        FROM   people p
        JOIN   family_children fc ON fc.person_id = p.id
        JOIN   families f         ON f.id = fc.family_id
        JOIN   people par         ON par.id IN (f.husband, f.wife)
        WHERE  {condition}

        UNION ALL

        SELECT 'sibling', sib.id, sib.name, sib.gender, sib.born, sib.died,
            NULL, NULL, sib.source_file, NULL
        FROM   people p
        JOIN   family_children fc ON fc.person_id = p.id
        JOIN   families f         ON f.id = fc.family_id
        JOIN   family_children fc2 ON fc2.family_id = f.id
        JOIN   people sib         ON sib.id = fc2.person_id
        WHERE  {condition}
        AND  sib.id != p.id

        UNION ALL

        SELECT 'spouse', spouse.id, spouse.name, spouse.gender, spouse.born, spouse.died,
            f.married, f.divorced, spouse.source_file, NULL
        FROM   people p
        JOIN   families f    ON f.husband = p.id OR f.wife = p.id
        JOIN   people spouse ON spouse.id = CASE
                WHEN f.husband = p.id THEN f.wife
                ELSE f.husband END
        WHERE  {condition}

        UNION ALL

        SELECT 'child', child.id, child.name, child.gender, child.born, child.died,
            NULL, NULL, child.source_file, NULL
        FROM   people p
        JOIN   families f         ON f.husband = p.id OR f.wife = p.id
        JOIN   family_children fc ON fc.family_id = f.id
        JOIN   people child       ON child.id = fc.person_id
        WHERE  {condition}
        """
        return self.query(sql)
