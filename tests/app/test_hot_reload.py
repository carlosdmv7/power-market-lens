"""drop_stale: every app module goes when any one of them changed on disk."""

import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "app"))

import hot_reload  # noqa: E402


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    for name in ("helper_a", "helper_b"):
        (tmp_path / f"{name}.py").write_text(f"NAME = {name!r}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delattr(sys, hot_reload._STATE_ATTR, raising=False)
    yield tmp_path
    for name in ("helper_a", "helper_b"):
        sys.modules.pop(name, None)


def import_helpers():
    import helper_a  # noqa: F401, PLC0415
    import helper_b  # noqa: F401, PLC0415


def test_first_run_drops_everything_so_a_stale_server_is_repaired(app_dir):
    import_helpers()

    assert sorted(hot_reload.drop_stale(app_dir)) == ["helper_a", "helper_b"]


def test_unchanged_files_are_kept(app_dir):
    import_helpers()
    hot_reload.drop_stale(app_dir)
    import_helpers()

    assert hot_reload.drop_stale(app_dir) == []


def test_one_changed_file_drops_all_of_them(app_dir):
    import_helpers()
    hot_reload.drop_stale(app_dir)
    import_helpers()
    future = time.time() + 5
    os.utime(app_dir / "helper_a.py", (future, future))  # a deploy rewrote it

    # helper_b did not change, but it may hold objects from helper_a: it goes too.
    assert sorted(hot_reload.drop_stale(app_dir)) == ["helper_a", "helper_b"]
