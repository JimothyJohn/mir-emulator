"""Selection-rule tests: this logic decides what the whole repo tracks."""

from hypothesis import given
from hypothesis import strategies as st
from mir_spec_scraper.versions import format_version, parse_version, select_tracked


def test_parse_plain_and_four_part_versions():
    assert parse_version("REST API 3.5.4") == (3, 5, 4)
    assert parse_version("mir_mir250_rest_api_2.13.3.1.zip") == (2, 13, 3, 1)
    assert parse_version("no version here") is None
    assert parse_version("v1.2") is None  # two-part strings are not release files


def test_every_major_gets_its_newest_four_minor_lines():
    versions = [
        (3, 8, 1),
        (3, 8, 0),
        (3, 7, 2),
        (3, 6, 7),
        (3, 5, 6),
        (3, 5, 4),
        (3, 4, 0),  # 5th-newest 3.x minor line: not selected
        (2, 14, 7),
        (2, 13, 5, 4),
        (2, 13, 5, 3),
        (2, 12, 0, 4),
        (2, 10, 5, 8),
        (2, 9, 0, 2),  # 5th-newest 2.x minor line: not selected
    ]
    assert select_tracked(versions) == [
        (3, 8, 1),
        (3, 7, 2),
        (3, 6, 7),
        (3, 5, 6),
        (2, 14, 7),
        (2, 13, 5, 4),
        (2, 12, 0, 4),
        (2, 10, 5, 8),
    ]


def test_latest_patch_wins_within_a_minor_line():
    versions = [(2, 13, 0, 4), (2, 13, 5, 1), (2, 13, 5, 4), (2, 13, 3)]
    assert select_tracked(versions) == [(2, 13, 5, 4)]


def test_fewer_minor_lines_than_slots():
    assert select_tracked([(3, 5, 4), (3, 4, 1)]) == [(3, 5, 4), (3, 4, 1)]
    assert select_tracked([(3, 5, 4)]) == [(3, 5, 4)]
    assert select_tracked([]) == []


def test_minors_per_major_knob():
    versions = [(3, 5, 4), (3, 4, 1), (3, 3, 0), (2, 14, 7), (2, 13, 3)]
    assert select_tracked(versions, minors_per_major=1) == [(3, 5, 4), (2, 14, 7)]
    assert select_tracked(versions, minors_per_major=2) == [
        (3, 5, 4),
        (3, 4, 1),
        (2, 14, 7),
        (2, 13, 3),
    ]


def test_duplicates_and_order_do_not_matter():
    versions = [(3, 5, 4), (2, 14, 7), (3, 5, 4), (1, 9, 0), (2, 14, 7)]
    assert select_tracked(list(reversed(versions))) == [(3, 5, 4), (2, 14, 7), (1, 9, 0)]


version_strategy = st.tuples(st.integers(1, 9), st.integers(0, 20), st.integers(0, 20))


@given(st.lists(version_strategy, max_size=30), st.integers(1, 5))
def test_selection_invariants(versions, minors_per_major):
    picked = select_tracked(versions, minors_per_major=minors_per_major)
    majors = {v[0] for v in versions}
    assert len(picked) <= minors_per_major * len(majors)
    assert len(set(picked)) == len(picked)
    assert picked == sorted(picked, reverse=True)
    assert set(picked) <= set(versions)
    if versions:
        assert picked[0] == max(set(versions))  # newest version is always tracked
    # every major present is represented by its own latest version
    for major in majors:
        latest_in_major = max(v for v in versions if v[0] == major)
        assert latest_in_major in picked
    # at most one pick per (major, minor) line, and it is that line's latest
    lines = [(v[0], v[1]) for v in picked]
    assert len(lines) == len(set(lines))
    for v in picked:
        assert v == max(w for w in versions if (w[0], w[1]) == (v[0], v[1]))
    # per major, no more than the requested number of minor lines
    for major in majors:
        assert sum(1 for line in lines if line[0] == major) <= minors_per_major


def test_format_round_trip():
    assert format_version((2, 13, 3, 1)) == "2.13.3.1"
    assert parse_version(format_version((3, 5, 4))) == (3, 5, 4)
