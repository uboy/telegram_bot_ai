"""
Regression tests for contamination-control and canonicality scoring (RAGSVC-014/015).

These tests verify that:
- Exact field match generates canonicality boost.
- Status/archive-labeled pages receive broad_scope_marker contamination penalty
  when the query is NOT about status/archive.
- The same pages are NOT penalized by broad_scope_marker when the query IS about status.
- List/table-heavy content receives list_or_table_shape contamination penalty.
- Content-only matches (no field coverage) receive content_only_match penalty.
- Canonical page ranks above a noisy status/archive page end-to-end.
- When there is no canonicality/contamination signal, original order is preserved.
"""
import os

os.environ["MYSQL_URL"] = ""
os.environ.setdefault("DB_PATH", "data/test-rag-contamination.db")

from shared.rag_system import (
    _annotate_candidates_with_canonicality,
    _order_candidates_by_canonicality,
)


def _candidate(
    *,
    content: str,
    source_path: str = "wiki/doc",
    doc_title: str = "",
    section_title: str = "",
    section_path: str = "",
    **overrides,
) -> dict:
    """Minimal candidate dict for canonicality/contamination unit tests."""
    base = {
        "content": content,
        "source_path": source_path,
        "metadata": {
            "doc_title": doc_title,
            "section_title": section_title,
            "section_path": section_path,
        },
        # Pre-annotated field-specificity keys (normally set by
        # _order_candidates_by_query_field_specificity).
        "_query_field_exact_match": False,
        "_query_field_best_exact": False,
        "_query_field_best_coverage": 0.0,
        "_query_field_best_precision": 0.0,
        "_query_field_term_hits": 0,
        "_query_field_distinctive_hits": 0,
        "_query_field_specificity_score": 0.0,
        # Pre-annotated family keys (normally set by
        # _annotate_candidates_with_family_support).
        "_family_channel_count": 1,
        "_family_candidate_count": 1,
        "_family_support_rrf": 0.0,
        "_family_rank": 1,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Canonicality signal tests
# ---------------------------------------------------------------------------

def test_exact_field_match_adds_canonicality_score():
    """Exact field match should add at least 3.0 to canonicality_score."""
    c = _candidate(
        content="Install repo tool on Ubuntu by running sudo apt-get install repo.",
        source_path="wiki/Infrastructure/Server-Setup-Linux",
        doc_title="Server Setup Linux",
        section_title="Install repo tool",
        section_path="Infrastructure > Server Setup Linux > Install repo tool",
        _query_field_exact_match=True,
        _query_field_best_coverage=0.65,
        _query_field_best_precision=0.5,
        _query_field_term_hits=3,
        _query_field_distinctive_hits=1,
    )

    annotated = _annotate_candidates_with_canonicality(
        [c], query="how to install repo tool on ubuntu"
    )

    row = annotated[0]
    assert float(row["_canonicality_score"]) >= 3.0, (
        "exact_field_match should contribute at least 3.0 to canonicality_score"
    )
    assert "exact_field_match" in row["_canonicality_reason"]
    assert float(row["_canonicality_net_score"]) > 0.0


def test_focused_field_coverage_adds_canonicality():
    """High field coverage without exact match should still give positive canonicality."""
    c = _candidate(
        content="Sync the code with repo sync -c -j 8 before building.",
        source_path="wiki/Sync-Build/Sync-Build",
        doc_title="Sync&Build",
        section_title="Initialize repository and sync code",
        section_path="Sync&Build > Initialize repository and sync code",
        _query_field_best_coverage=0.7,
        _query_field_best_precision=0.4,
        _query_field_term_hits=3,
    )

    annotated = _annotate_candidates_with_canonicality(
        [c], query="how to sync and build the repository"
    )

    row = annotated[0]
    assert float(row["_canonicality_score"]) > 0.0
    assert "focused_field_coverage" in row["_canonicality_reason"]


# ---------------------------------------------------------------------------
# Contamination signal tests
# ---------------------------------------------------------------------------

def test_status_archive_page_gets_broad_scope_marker_penalty_for_non_status_query():
    """A page with 'status'/'archive' in path/title should receive broad_scope_marker
    contamination when the query is NOT about status."""
    status_page = _candidate(
        content=(
            "- repo: available\n"
            "- ubuntu: supported\n"
            "- install: pending\n"
            "- tool: ok\n"
            "- sync: failed\n"
        ),
        source_path="wiki/DEV_API_STATUS",
        doc_title="DEV_API_STATUS",
        section_title="Environment status overview",
        section_path="DEV_API_STATUS > Status overview",
        _query_field_best_coverage=0.2,
        _query_field_term_hits=1,
    )

    annotated = _annotate_candidates_with_canonicality(
        [status_page], query="how to install repo tool on ubuntu"
    )

    row = annotated[0]
    assert "broad_scope_marker" in row["_contamination_reason"], (
        "Status-labeled page should receive broad_scope_marker penalty "
        "when query is not about status/archive"
    )
    assert float(row["_contamination_penalty"]) > 0.0


def test_status_page_broad_scope_penalty_suppressed_for_status_query():
    """When the query explicitly asks about status, broad_scope_marker should NOT
    be applied to a status-labeled page."""
    status_page = _candidate(
        content=(
            "- repo: available\n"
            "- ubuntu: supported\n"
            "- install: pending\n"
            "- tool: ok\n"
            "- sync: failed\n"
        ),
        source_path="wiki/DEV_API_STATUS",
        doc_title="DEV_API_STATUS",
        section_title="Environment status overview",
        section_path="DEV_API_STATUS > Status overview",
        _query_field_best_coverage=0.2,
        _query_field_term_hits=1,
    )

    annotated = _annotate_candidates_with_canonicality(
        [status_page], query="what is the current repo tool status"
    )

    row = annotated[0]
    assert "broad_scope_marker" not in row["_contamination_reason"], (
        "Status-labeled page should NOT be penalized by broad_scope_marker "
        "when the query explicitly asks about status"
    )


def test_list_heavy_content_gets_list_or_table_shape_penalty():
    """A page whose content is mostly bullet/list lines should receive the
    list_or_table_shape contamination penalty."""
    list_page = _candidate(
        content=(
            "- build target arm64\n"
            "- configure release mode\n"
            "- set cpu architecture\n"
            "- enable build ninja\n"
            "- add product rk3568\n"
            "- specify output directory\n"
            "- run prebuilts script\n"
            "build configuration overview"
        ),
        source_path="wiki/Build-Config",
        doc_title="Build Configuration Inventory",
        section_title="All build parameters",
        section_path="Build > Inventory > Build Configuration",
        _query_field_best_coverage=0.3,
        _query_field_term_hits=1,
    )

    annotated = _annotate_candidates_with_canonicality(
        [list_page], query="what is the build configuration for arm64 architecture"
    )

    row = annotated[0]
    assert "list_or_table_shape" in row["_contamination_reason"], (
        "List-heavy content should receive list_or_table_shape contamination penalty"
    )
    assert float(row["_contamination_penalty"]) >= 0.5


def test_content_only_match_without_field_coverage_gets_penalty():
    """A page that matches query terms only in content (not in structural fields)
    should receive the content_only_match contamination penalty."""
    broad_notes = _candidate(
        content=(
            "This page covers many topics including patch application, linux setup, "
            "previewer configuration, build tools, and other unrelated items."
        ),
        source_path="wiki/General-Notes",
        doc_title="General Notes",
        section_title="Overview notes",
        section_path="General Notes > Overview",
        _query_field_term_hits=0,
        _query_field_best_coverage=0.0,
    )

    annotated = _annotate_candidates_with_canonicality(
        [broad_notes], query="what patch should i apply for linux previewer"
    )

    row = annotated[0]
    assert "content_only_match" in row["_contamination_reason"], (
        "Page with content hits but no field coverage should get content_only_match penalty"
    )
    assert float(row["_contamination_penalty"]) > 0.0


# ---------------------------------------------------------------------------
# Ordering tests
# ---------------------------------------------------------------------------

def test_canonical_page_ranked_above_status_archive_page():
    """A canonical page with exact field match should rank above a noisy
    DEV_API_STATUS-style page even when the status page appears first in input."""
    canonical = _candidate(
        content=(
            "Install repo tool on Ubuntu by running sudo apt-get install repo. "
            "Then run repo init to initialize the repository."
        ),
        source_path="wiki/Infrastructure/Server-Setup-Linux",
        doc_title="Server Setup Linux",
        section_title="Install repo tool",
        section_path="Infrastructure > Server Setup Linux > Install repo tool",
        _query_field_exact_match=True,
        _query_field_best_coverage=0.65,
        _query_field_best_precision=0.5,
        _query_field_term_hits=3,
        _query_field_distinctive_hits=1,
    )

    status_page = _candidate(
        content=(
            "- repo: available\n"
            "- ubuntu: supported\n"
            "- install: pending\n"
            "- tool: ok\n"
            "- sync: failed\n"
            "- build: running\n"
            "- configure: blocked\n"
        ),
        source_path="wiki/DEV_API_STATUS",
        doc_title="DEV_API_STATUS",
        section_title="Status overview",
        section_path="DEV_API_STATUS > Status overview",
        _query_field_best_coverage=0.2,
        _query_field_term_hits=1,
    )

    # Status page is deliberately placed first in input to verify ordering fixes it.
    ordered = _order_candidates_by_canonicality(
        [status_page, canonical],
        query="how to install repo tool on ubuntu",
    )

    assert ordered[0]["source_path"] == "wiki/Infrastructure/Server-Setup-Linux", (
        "Canonical page with exact field match should rank above status/archive page"
    )
    assert ordered[1]["source_path"] == "wiki/DEV_API_STATUS"

    # Canonicality net score of canonical must exceed that of status page.
    assert float(ordered[0]["_canonicality_net_score"]) > float(
        ordered[1]["_canonicality_net_score"]
    )


def test_canonical_page_ranked_above_list_inventory_page():
    """A focused canonical page beats a list-heavy inventory page."""
    canonical = _candidate(
        content=(
            "To configure the arm64 architecture build, set target_cpu=arm64 "
            "and run build.sh --product-name rk3568."
        ),
        source_path="wiki/Build-Guide/Configure-arm64",
        doc_title="Build Guide",
        section_title="Configure arm64 build",
        section_path="Build Guide > Configure arm64 build",
        _query_field_exact_match=True,
        _query_field_best_coverage=0.6,
        _query_field_term_hits=2,
    )

    list_page = _candidate(
        content=(
            "- build target arm64\n"
            "- configure release mode\n"
            "- set cpu architecture\n"
            "- enable build ninja\n"
            "- add product rk3568\n"
            "- specify output directory\n"
            "- run prebuilts script\n"
            "build configuration overview"
        ),
        source_path="wiki/Build-Config-Inventory",
        doc_title="Build Configuration Inventory",
        section_title="All build parameters",
        section_path="Build > Inventory > All Parameters",
        _query_field_best_coverage=0.3,
        _query_field_term_hits=1,
    )

    ordered = _order_candidates_by_canonicality(
        [list_page, canonical],
        query="how to configure the build for arm64 architecture",
    )

    assert ordered[0]["source_path"] == "wiki/Build-Guide/Configure-arm64", (
        "Canonical page should rank above list-heavy inventory page"
    )


def test_no_signal_preserves_original_order():
    """When no canonicality or contamination signal exists, original input order
    must be preserved."""
    a = _candidate(
        content="First document about alpha topic.",
        source_path="wiki/doc-a",
        doc_title="Doc A",
    )
    b = _candidate(
        content="Second document about beta topic.",
        source_path="wiki/doc-b",
        doc_title="Doc B",
    )
    c = _candidate(
        content="Third document about gamma topic.",
        source_path="wiki/doc-c",
        doc_title="Doc C",
    )

    ordered = _order_candidates_by_canonicality(
        [a, b, c],
        query="zzznomatchpossibletokens",
    )

    assert [row["source_path"] for row in ordered] == [
        "wiki/doc-a",
        "wiki/doc-b",
        "wiki/doc-c",
    ], "Original order must be preserved when there is no canonicality/contamination signal"
