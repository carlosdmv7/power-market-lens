"""The app's deploy requirements against the project's pins."""

from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).parents[2]


def pins(lines: list[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;#]+)", line)
        if match:
            found[match.group(1).lower()] = match.group(2)
    return found


def test_app_requirements_match_the_project_pins():
    # Cloud would otherwise run a different Streamlit or DuckDB than CI tests.
    project = pins(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"])
    app = pins((ROOT / "app" / "requirements.txt").read_text().splitlines())

    assert app, "app/requirements.txt pins nothing"
    assert {name: project.get(name) for name in app} == app
