# About this project

This is a lightweight genealogy package, supporting both manual editing of YAML genealogy files and browser GUI.

Each YAML file correspond to a "lineage", which includes descendants of a progenitor and their spouses. Multiple lineages can form a "community" where the same person can appear in multiple yaml files, e.g. as a child in one file and as a spouse in another. Each person has a unique ID. Data of all community members and their marital relationships is stored in gelealogy.db sqlite database, one per community. The DB synced to yaml files via yaml_to_sqlite.py script.

The project also include "faker" module generating fictional genealogies.

# Co-working rules

The project is developed on a Windows machine. For OS command-line operations (git, pytest etc.), use PowerShell. For Python scripts, use .venv Python.

Start with reading TODO.md where I document changes made manually since the latest session, and set agenda for the upcoming session.

If the code was changed manually, run tests, identify and fix bugs. When doing manual updates, I don't bother about tests. The existing tests will most likely fail. First, identify failures caused by incompatibility of older tests with the new functionality - these tests should be edited or removed. Then, identify failures caused by bugs in the new functionality itself. These should be fixed by editing the main code. Distinguish these 2 categories.

If no manual changes were made, or documentation only changed, skip testing. 

Proceed to tasks formulated in the TODO.md, "TODOS" section.

Then move to "Discussion points" of TODO.md (if exist) where I propose some new functionality for discussion - not for immediate implementation. These are ideas I'm not sure about, and I need your second opinion. Review these ideas and give feedback.

At the end of a session, I write you "close the session". It means: update your memory, clean up TODO.md, bump the patch version in `pyproject.toml` (0.3.0 → 0.3.1), commit and push. The package is installed from the git repo in other projects, and pip only reinstalls it when the version changes.
