"""Selection-rule tests: this logic decides what the whole repo tracks."""

from hypothesis import given
from hypothesis import strategies as st
from mir_spec_scraper.versions import format_version, parse_version, select_tracked


def test_parse_plain_and_four_part_versions():
    assert parse_version("REST API 3.5.4") == (3, 5, 4)
    assert parse_version("mir_mir250_rest_api_2.13.3.1.zip") == (2, 13, 3, 1)
    assert parse_version("no version here") is None
    assert parse_version("v1.2") is None  # two-part strings are not release files


def test_three_majors_pick_latest_minor_patch_each():
    versions = [
        (1, 9, 0),
        (2, 13, 3, 1),
        (2, 14, 7),
        (2, 14, 2),
        (3, 4, 1),
        (3, 5, 4),
        (3, 5, 2),
    ]
    assert select_tracked(versions) == [(3, 5, 4), (2, 14, 7), (1, 9, 0)]


def test_two_majors_fill_with_previous_minor_line_of_newest_major():
    versions = [(2, 14, 7), (2, 13, 3), (3, 4, 1), (3, 5, 4)]
    assert select_tracked(versions) == [(3, 5, 4), (3, 4, 1), (2, 14, 7)]


def test_single_major_falls_back_to_minor_lines():
    versions = [(3, 3, 0), (3, 4, 1), (3, 5, 4), (3, 5, 0)]
    assert select_tracked(versions) == [(3, 5, 4), (3, 4, 1), (3, 3, 0)]


def test_fewer_versions_than_slots():
    assert select_tracked([(3, 5, 4)]) == [(3, 5, 4)]
    assert select_tracked([]) == []


def test_duplicates_and_order_do_not_matter():
    versions = [(3, 5, 4), (2, 14, 7), (3, 5, 4), (1, 9, 0), (2, 14, 7)]
    assert select_tracked(list(reversed(versions))) == [(3, 5, 4), (2, 14, 7), (1, 9, 0)]


version_strategy = st.tuples(st.integers(1, 9), st.integers(0, 20), st.integers(0, 20))


@given(st.lists(version_strategy, max_size=30), st.integers(1, 5))
def test_selection_invariants(versions, majors):
    picked = select_tracked(versions, majors=majors)
    assert len(picked) <= majors
    assert len(set(picked)) == len(picked)
    assert picked == sorted(picked, reverse=True)
    assert set(picked) <= set(versions)
    if versions:
        assert picked[0] == max(set(versions))  # newest version is always tracked
    # every represented major contributes its own latest version
    for major in {v[0] for v in picked}:
        latest_in_major = max(v for v in versions if v[0] == major)
        assert latest_in_major in picked


def test_format_round_trip():
    assert format_version((2, 13, 3, 1)) == "2.13.3.1"
    assert parse_version(format_version((3, 5, 4))) == (3, 5, 4)
