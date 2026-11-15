"""The dbt exposure for the app lists exactly the marts the app queries.

The exposure is what puts the app in dbt's lineage and lets
`dbt build -s +exposure:streamlit_app` rebuild everything it depends on. It
is only useful while it is true, so this test reads both sides.
"""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).parents[2]


def test_the_exposure_lists_the_marts_the_app_reads():
    queried = set(re.findall(r"marts\.(\w+)", (ROOT / "app" / "data.py").read_text()))
    exposures = yaml.safe_load((ROOT / "transform" / "models" / "exposures.yml").read_text())
    (app,) = [e for e in exposures["exposures"] if e["name"] == "streamlit_app"]
    declared = {re.fullmatch(r"ref\('(\w+)'\)", ref).group(1) for ref in app["depends_on"]}

    assert declared == queried
