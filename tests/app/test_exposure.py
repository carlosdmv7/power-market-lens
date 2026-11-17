"""The dbt exposure for the app lists exactly the tables the app queries.

The exposure is what puts the app in dbt's lineage and lets
`dbt build -s +exposure:streamlit_app` rebuild everything it depends on. It
is only useful while it is true, so this test reads both sides: the marts
(`ref`) and the monitor's table, which is a dbt source (`source`).
"""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).parents[2]


def test_the_exposure_lists_the_tables_the_app_reads():
    code = (ROOT / "app" / "data.py").read_text()
    queried = {f"marts.{t}" for t in re.findall(r"marts\.(\w+)", code)} | {
        f"monitor.{t}" for t in re.findall(r"monitor\.(\w+)", code)
    }
    exposures = yaml.safe_load((ROOT / "transform" / "models" / "exposures.yml").read_text())
    (app,) = [e for e in exposures["exposures"] if e["name"] == "streamlit_app"]
    declared = set()
    for dependency in app["depends_on"]:
        if ref := re.fullmatch(r"ref\('(\w+)'\)", dependency):
            declared.add(f"marts.{ref.group(1)}")
        elif source := re.fullmatch(r"source\('(\w+)', '(\w+)'\)", dependency):
            declared.add(f"{source.group(1)}.{source.group(2)}")

    assert declared == queried
