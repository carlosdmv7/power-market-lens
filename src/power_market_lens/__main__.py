"""Entrypoint: `uv run python -m power_market_lens` (or `make run`)."""

import logging

from power_market_lens.config import load_settings

log = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    log.info("Running in %s", settings.env)


if __name__ == "__main__":
    main()
