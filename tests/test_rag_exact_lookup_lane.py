"""
Regression tests for the exact-lookup and navigation retrieval lane (RAGSVC-007/018).

Covers:
- _is_exact_lookup_query: which queries trigger the lane (navigation/reference phrasing)
- _is_exact_lookup_query: compound HOWTO queries must NOT trigger the lane
- _apply_exact_lookup_lane: anchor selection from field-annotated candidates
- _apply_exact_lookup_lane: confidence threshold — degrade gracefully when no anchor found
- _apply_exact_lookup_lane: focusing behavior (anchor-doc rows first + max 2 others)
- _apply_exact_lookup_lane: query_mode values exposed correctly
- _apply_exact_lookup_lane: non-exact-lookup queries return "generalized" mode unchanged
"""
import os

os.environ["MYSQL_URL"] = ""
os.environ.setdefault("DB_PATH", "data/test-rag-exact-lookup.db")

from backend.api.routes.rag import (
    _is_exact_lookup_query,
    _apply_exact_lookup_lane,
)


# ---------------------------------------------------------------------------
# _is_exact_lookup_query — detection tests
# ---------------------------------------------------------------------------

def test_exact_lookup_where_is_phrasing():
    assert _is_exact_lookup_query("where is the api reference") is True


def test_exact_lookup_where_can_i_find():
    assert _is_exact_lookup_query("where can i find official documentation") is True


def test_exact_lookup_what_patch():
    assert _is_exact_lookup_query("what patch should i apply for linux previewer") is True


def test_exact_lookup_which_patch():
    assert _is_exact_lookup_query("which patch do i need for the master branch") is True


def test_exact_lookup_api_reference():
    assert _is_exact_lookup_query("api reference for hdc tool") is True


def test_exact_lookup_official_documentation():
    assert _is_exact_lookup_query("official documentation for arkui") is True


def test_exact_lookup_official_docs():
    assert _is_exact_lookup_query("where do i find official docs for the sdk") is True


def test_exact_lookup_which_page():
    # "install guide" is excluded by compound-HOWTO gate; use unambiguous navigation phrasing
    assert _is_exact_lookup_query("which page has the sdk api reference") is True


def test_exact_lookup_russian_где_найти():
    assert _is_exact_lookup_query("где найти официальную документацию") is True


def test_exact_lookup_russian_какой_патч():
    assert _is_exact_lookup_query("какой патч применить для linux") is True


def test_not_exact_lookup_broad_semantic():
    """Open-ended broad semantic query must NOT trigger the lane."""
    assert _is_exact_lookup_query("what is the difference between components and modules") is False


def test_not_exact_lookup_compound_howto():
    """Compound HOWTO queries (2+ action terms + cue) must NOT trigger the lane."""
    assert _is_exact_lookup_query("how to sync and build the openharmony repository") is False


def test_not_exact_lookup_compound_howto_install_configure():
    """Another compound HOWTO must not fire the lane."""
    assert _is_exact_lookup_query("how to install and configure the sdk environment") is False


def test_not_exact_lookup_empty():
    assert _is_exact_lookup_query("") is False


def test_not_exact_lookup_generic_question():
    assert _is_exact_lookup_query("what are the build requirements") is False


# ---------------------------------------------------------------------------
# _apply_exact_lookup_lane — helper to build annotated candidates
# ---------------------------------------------------------------------------

def _nav_candidate(
    *,
    source_path: str,
    doc_title: str = "",
    section_title: str = "",
    content: str = "Some content.",
    **field_overrides,
) -> dict:
    base = {
        "content": content,
        "source_path": source_path,
        "metadata": {
            "doc_title": doc_title,
            "section_title": section_title,
        },
        "_query_field_exact_match": False,
        "_query_field_best_exact": False,
        "_query_field_best_coverage": 0.0,
        "_query_field_best_precision": 0.0,
        "_query_field_term_hits": 0,
        "_query_field_distinctive_hits": 0,
        "_query_field_specificity_score": 0.0,
        "_canonicality_score": 0.0,
        "_family_channel_count": 1,
        "_family_candidate_count": 1,
        "_family_support_rrf": 0.0,
        "_family_rank": 1,
    }
    base.update(field_overrides)
    return base


# ---------------------------------------------------------------------------
# _apply_exact_lookup_lane — lane behavior tests
# ---------------------------------------------------------------------------

def test_lane_returns_generalized_for_non_lookup_query():
    """Non-lookup queries return original rows unchanged with mode=generalized."""
    rows = [
        _nav_candidate(source_path="wiki/doc-a", _query_field_exact_match=True),
        _nav_candidate(source_path="wiki/doc-b"),
    ]
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "what are the build system requirements", rows
    )
    assert mode == "generalized"
    assert family is None
    assert reason is None
    assert focused is rows  # same object, not reordered


def test_lane_returns_generalized_for_empty_rows():
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "where is the api reference", []
    )
    assert mode == "generalized"
    assert focused == []


def test_lane_fires_for_exact_field_match():
    """Exact field match → anchor found → mode=exact_lookup."""
    canonical = _nav_candidate(
        source_path="wiki/ArkUI-API-Reference",
        doc_title="ArkUI API Reference",
        _query_field_exact_match=True,
        _query_field_best_coverage=0.7,
    )
    noise = _nav_candidate(source_path="wiki/Misc-Notes")

    focused, mode, family, reason = _apply_exact_lookup_lane(
        "where can i find the arkui api reference",
        [canonical, noise],
    )
    assert mode == "exact_lookup"
    assert reason == "exact_field_match"
    assert family == "wiki/ArkUI-API-Reference"


def test_lane_fires_for_high_field_coverage():
    """High field coverage (>= 0.45) → anchor found without exact match."""
    candidate = _nav_candidate(
        source_path="wiki/Linux-Previewer-Patch",
        doc_title="Linux Previewer Patch",
        _query_field_best_coverage=0.55,
    )
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "what patch should i apply for linux previewer",
        [candidate],
    )
    assert mode == "exact_lookup"
    assert "field_coverage" in reason
    assert family == "wiki/Linux-Previewer-Patch"


def test_lane_fires_for_canonical_distinctive_hit():
    """Canonical score + distinctive hit → anchor found."""
    candidate = _nav_candidate(
        source_path="wiki/Server-Setup-Ubuntu",
        _query_field_distinctive_hits=1,
        _canonicality_score=2.0,  # >= _EXACT_LOOKUP_MIN_CANONICALITY (1.5)
    )
    # Avoid "guide"/"steps" cue words that would trigger compound-HOWTO gate
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "where do i find the official documentation for ubuntu server",
        [candidate],
    )
    assert mode == "exact_lookup"
    assert reason == "canonical_distinctive_hit"


def test_lane_degrades_when_no_confident_anchor():
    """When no candidate meets confidence threshold, degrade gracefully."""
    rows = [
        _nav_candidate(source_path="wiki/generic-A", _query_field_best_coverage=0.1),
        _nav_candidate(source_path="wiki/generic-B", _query_field_best_coverage=0.2),
    ]
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "where is the official documentation",
        rows,
    )
    assert mode == "exact_lookup_degraded"
    assert family is None
    assert reason == "no_confident_anchor"
    # Original rows unchanged
    assert focused is rows


def test_lane_focuses_anchor_doc_rows_first():
    """When lane fires, anchor-document rows appear first, others limited to 2."""
    anchor_doc = "wiki/Official-SDK-Docs"
    rows = [
        _nav_candidate(source_path=anchor_doc, doc_title="Official SDK Docs", _query_field_exact_match=True),
        _nav_candidate(source_path=anchor_doc, doc_title="Official SDK Docs", content="Section 2."),
        _nav_candidate(source_path="wiki/Other-A"),
        _nav_candidate(source_path="wiki/Other-B"),
        _nav_candidate(source_path="wiki/Other-C"),
    ]
    focused, mode, family, reason = _apply_exact_lookup_lane(
        "where can i find official documentation for the sdk",
        rows,
    )
    assert mode == "exact_lookup"
    # Anchor-doc rows come first
    assert focused[0]["source_path"] == anchor_doc
    assert focused[1]["source_path"] == anchor_doc
    # At most 2 rows from other families
    other_paths = [r["source_path"] for r in focused if r["source_path"] != anchor_doc]
    assert len(other_paths) <= 2
    # Other-C must not be included (only 2 others allowed)
    assert "wiki/Other-C" not in other_paths


def test_lane_anchor_family_is_source_path():
    """anchor_family should be the anchor's source_path."""
    candidate = _nav_candidate(
        source_path="wiki/HDC-Tool-Reference",
        _query_field_exact_match=True,
    )
    _, _, family, _ = _apply_exact_lookup_lane(
        "api reference for hdc tool",
        [candidate],
    )
    assert family == "wiki/HDC-Tool-Reference"


def test_lane_anchor_family_falls_back_to_doc_title_when_no_source_path():
    """When source_path is empty, fall back to doc_title for anchor_family."""
    candidate = _nav_candidate(
        source_path="",
        doc_title="ArkUI Component Reference",
        _query_field_exact_match=True,
    )
    _, _, family, _ = _apply_exact_lookup_lane(
        "where is the arkui component reference",
        [candidate],
    )
    assert family == "ArkUI Component Reference"
