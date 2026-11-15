"""Re-record the real Eurostat responses the Eurostat tests run against.

    uv run python scripts/record_eurostat_fixtures.py

Makes exactly the requests the loader makes (power_market_lens.ingest.eurostat),
so the fixtures are what production parses. No credentials are involved.
A re-recording differs from the last one only when Eurostat has published a
new version; commit it only when a test needs the new content.
"""

import json
from pathlib import Path

from power_market_lens.ingest.eurostat import DATASETS, fetch_dataset, updated_at

FIXTURES = Path(__file__).parents[1] / "tests" / "ingest" / "fixtures" / "eurostat"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for code, dataset in DATASETS.items():
        doc = fetch_dataset(dataset)
        path = FIXTURES / f"recorded_{code}.json"
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"{path.name}: {len(doc['value'])} values, updated {updated_at(doc):%Y-%m-%d}")


if __name__ == "__main__":
    main()
