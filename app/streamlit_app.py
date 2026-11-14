"""Power Market Lens: European day-ahead power prices, generation and flows.

    streamlit run app/streamlit_app.py

The entrypoint only routes: each page lives in app/views/. The look comes from
the portfolio design system through two native mechanisms only:
`.streamlit/config.toml` theme keys and the Altair theme registered by
`theme.py` (shared byte-for-byte with my other apps). No CSS injection.
"""

import importlib
import logging
from pathlib import Path

import hot_reload

# Before anything imports a helper: a Streamlit Cloud redeploy pulls new code
# into the running server, and without this the pages would run against the old
# charts/mix/data/theme still cached in sys.modules (see app/hot_reload.py).
# The reloader is itself an app module a deploy can change, so reload it first.
importlib.reload(hot_reload)
hot_reload.drop_stale(Path(__file__).parent)

import duckdb  # noqa: E402
import streamlit as st  # noqa: E402

log = logging.getLogger("power_market_lens.app")

VIEWS = Path(__file__).parent / "views"

st.set_page_config(
    page_title="Power Market Lens",
    page_icon=str(Path(__file__).parent / "assets" / "favicon.png"),
    layout="wide",
)

page = st.navigation(
    [
        st.Page(VIEWS / "zone.py", title="Zone", icon=":material/show_chart:", default=True),
        st.Page(VIEWS / "map.py", title="Map", icon=":material/map:", url_path="map"),
    ],
    position="top",
)
try:
    page.run()
except (duckdb.Error, RuntimeError):
    # The warehouse cannot be reached or queried: MotherDuck is down, the
    # token is missing or no longer valid, a mart is not there. A visitor can
    # do nothing with a traceback, and it would show internals on a public
    # page; the detail goes to the server log instead. `st.stop()` and reruns
    # are not caught: they raise Streamlit's own control-flow exceptions.
    log.exception("The page could not read the warehouse")
    st.error(
        "The data is temporarily unavailable. This is a problem on our side, not yours: "
        "please try again in a few minutes.",
        icon=":material/cloud_off:",
    )
