"""Power Market Lens: European day-ahead power prices, generation and flows.

    streamlit run app/streamlit_app.py

The entrypoint only routes: each page lives in app/views/. The look comes from
the portfolio design system through two native mechanisms only:
`.streamlit/config.toml` theme keys and the Altair theme registered by
`theme.py` (shared byte-for-byte with my other apps). No CSS injection.
"""

import importlib
from pathlib import Path

import hot_reload

# Before anything imports a helper: a Streamlit Cloud redeploy pulls new code
# into the running server, and without this the pages would run against the old
# charts/mix/data/theme still cached in sys.modules (see app/hot_reload.py).
# The reloader is itself an app module a deploy can change, so reload it first.
importlib.reload(hot_reload)
hot_reload.drop_stale(Path(__file__).parent)

import streamlit as st  # noqa: E402

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
page.run()
