from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "app"))

import mix  # noqa: E402

# The mart's grouping, as the technology_mapping seed sets it (ADR-004).
GROUP_OF = {
    "B04": "Fossil",
    "B05": "Fossil",
    "B11": "Hydro & other",
    "B14": "Nuclear",
    "B16": "Solar",
    "B18": "Wind",
    "B19": "Wind",
}


def generation(rows):
    frame = pd.DataFrame(rows, columns=["time", "psr_type", "generation_mw"])
    return frame.assign(display_group=frame["psr_type"].map(GROUP_OF))


def test_five_groups_at_most():
    assert len(mix.GROUPS) == 5  # the brand's categorical limit


def test_a_group_without_a_colour_fails_loudly():
    t = pd.Timestamp("2026-10-11 12:00")
    frame = generation([(t, "B16", 1.0)]).assign(display_group="Storage")

    with pytest.raises(ValueError, match="Storage"):
        mix.grouped_mix(frame)


def test_groups_sum_their_types_and_shares_add_up():
    t = pd.Timestamp("2026-10-11 12:00")
    grouped = mix.grouped_mix(
        generation(
            [
                (t, "B18", 100.0),  # offshore wind
                (t, "B19", 300.0),  # onshore wind
                (t, "B04", 200.0),  # gas
                (t, "B05", 50.0),  # coal
                (t, "B11", 350.0),  # run-of-river hydro
            ]
        )
    )

    by_group = grouped.set_index("group")["generation_mw"].to_dict()
    assert by_group == {"Fossil": 250.0, "Hydro & other": 350.0, "Wind": 400.0}
    assert grouped["share"].sum() == 1.0


def test_stack_order_is_fixed_whatever_the_data():
    # Colour follows the entity: a range without nuclear must not shift the
    # remaining groups to other colours or positions.
    t = pd.Timestamp("2026-10-11 12:00")
    grouped = mix.grouped_mix(generation([(t, "B19", 1.0), (t, "B16", 1.0)]))

    assert list(grouped["group"].cat.categories) == mix.GROUP_ORDER
    assert list(grouped["group"]) == ["Solar", "Wind"]


def test_shares_rank_groups_over_the_period():
    t1, t2 = pd.Timestamp("2026-10-11 12:00"), pd.Timestamp("2026-10-11 13:00")
    grouped = mix.grouped_mix(
        generation([(t1, "B16", 300.0), (t1, "B14", 100.0), (t2, "B16", 100.0), (t2, "B14", 500.0)])
    )

    top = mix.shares(grouped)

    assert list(top.index) == ["Nuclear", "Solar"]
    assert round(top.iloc[0], 2) == 0.6
