"""
Committed deterministic regressions for multicorpus live failure shapes (RAGMULTI-002/003).

Failure classes covered:
- broad_procedure_miss: procedural query matched wrong doc (e.g. "how to build rk3568"
  returning test-generator page instead of sync-build page)
- contamination_miss: navigation/reference query returned a status/issue-tracker page
  (e.g. "where is arkui api reference" returning "Idlize issues" page)
- navigation_miss: structural placement query returned detail doc instead of overview
  (e.g. "where are UI interfaces placed in c-api" returning callbacks guide)
- setup_canonical_miss: setup query returned archived/old pages instead of current setup
  (e.g. "what host setup is recommended" returning archive pages)
- exact_lookup_procedural_miss: specific fix query returned wrong how-to doc
  (e.g. "how to fix previewer white screen" returning SDK guide instead of fix-rendering page)
- source_manifest_miss: manifest workflow query returned unrelated build-test page
  (e.g. "how to share feature manifest snapshot" returning e2e test page)

All tests use synthetic data only — no real corpus content is committed.
"""

import os

os.environ["MYSQL_URL"] = ""
os.environ.setdefault("DB_PATH", "data/test-rag-multicorpus-regressions.db")

from shared.rag_system import (
    _order_candidates_by_query_field_specificity,
    _annotate_candidates_with_canonicality,
    _order_candidates_by_canonicality,
)
from backend.api.routes.rag import _focus_compound_howto_rows


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _candidate(
    *,
    content: str,
    source_path: str = "wiki/doc",
    doc_title: str = "",
    section_title: str = "",
    section_path: str = "",
    **overrides,
) -> dict:
    base = {
        "content": content,
        "source_path": source_path,
        "metadata": {
            "doc_title": doc_title,
            "section_title": section_title,
            "section_path": section_path,
        },
        "_query_field_exact_match": False,
        "_query_field_best_exact": False,
        "_query_field_best_coverage": 0.0,
        "_query_field_best_precision": 0.0,
        "_query_field_term_hits": 0,
        "_query_field_distinctive_hits": 0,
        "_query_field_specificity_score": 0.0,
        "_family_channel_count": 1,
        "_family_candidate_count": 1,
        "_family_support_rrf": 0.0,
        "_family_rank": 1,
    }
    base.update(overrides)
    return base


def _ranked_sources(query: str, candidates: list[dict]) -> list[str]:
    """Run field-specificity ranking and return ordered source_path list."""
    ranked = _order_candidates_by_query_field_specificity(candidates, query=query)
    return [r["source_path"] for r in ranked]


# ---------------------------------------------------------------------------
# broad_procedure_miss
# ---------------------------------------------------------------------------

def test_broad_procedure_rk3568_prefers_build_steps_over_test_generator():
    """
    'how to build rk3568': field-specificity ranking must prefer the doc that
    contains 'rk3568' in its title/section over a test-generator page that only
    mentions 'build' incidentally.
    """
    sync_build = _candidate(
        content="Run ./build.sh --product-name rk3568 --ccache",
        source_path="wiki/Sync%26Build/Sync%26Build",
        doc_title="Sync&Build",
        section_title="Build rk3568 product",
        section_path="Sync&Build > Build rk3568 product",
    )
    test_gen = _candidate(
        content="Build the idlize test generator before running e2e.",
        source_path="wiki/Features/Test-generator/How-to-build-and-run-generator",
        doc_title="How to build and run generator",
        section_title="How to build and run generator",
        section_path="Features > Test generator > How to build and run generator",
    )

    ranked = _ranked_sources("how to build rk3568", [test_gen, sync_build])

    assert ranked[0] == "wiki/Sync%26Build/Sync%26Build", (
        "Build rk3568 page must rank above test generator page"
    )


def test_broad_procedure_sdk_windows_prefers_sync_build_over_sdk_guide():
    """
    'how to build ohos sdk for windows': prefers the direct build-step page
    over a page that only describes where to find the resulting SDK artifacts.
    """
    sync_build = _candidate(
        content="./build.sh --product-name ohos-sdk --gn-args sdk_platform=win --ccache",
        source_path="wiki/Sync%26Build/Sync%26Build",
        doc_title="Sync&Build",
        section_title="Build SDK for windows",
        section_path="Sync&Build > Build SDK for windows",
    )
    sdk_guide = _candidate(
        content="Go to out/sdk/ohos-sdk/windows and copy ets, js, previewer zip files.",
        source_path="wiki/Development/How-to-get-previewer-and-SDK",
        doc_title="How to get previewer and SDK for ACE FW",
        section_title="How to add extra logging in sdk and obtain logs from previewer:",
        section_path="Development > How to get previewer and SDK",
    )

    ranked = _ranked_sources("how to build ohos sdk for windows only", [sdk_guide, sync_build])

    assert ranked[0] == "wiki/Sync%26Build/Sync%26Build", (
        "Sync&Build build steps must rank above SDK artifact guide"
    )


def test_compound_howto_focus_excludes_test_generator_for_sync_and_build():
    """
    'how to sync and build': _focus_compound_howto_rows must exclude test
    generator pages (no 'sync' term in their section/title) and keep only
    rows from the sync-build family.
    """
    sync_build = {
        "content": "repo init ... repo sync -c -j 8 ... build/prebuilts_download.sh",
        "source_path": "wiki/Sync%26Build/Sync%26Build",
        "metadata": {
            "doc_title": "Sync&Build",
            "section_title": "Initialize repository and sync code",
            "section_path": "Sync&Build > Initialize repository and sync code",
            "section_path_norm": "sync&build > initialize repository and sync code",
        },
    }
    test_gen = {
        "content": "Build the generator project with npm run build.",
        "source_path": "wiki/Features/Test-generator/How-to-build-and-run-generator",
        "metadata": {
            "doc_title": "How to build and run generator",
            "section_title": "How to build and run generator",
            "section_path": "Features > Test generator > How to build and run generator",
            "section_path_norm": "features > test generator > how to build and run generator",
        },
    }

    focused = _focus_compound_howto_rows("how to sync and build", [test_gen, sync_build])

    assert all(r["source_path"] == "wiki/Sync%26Build/Sync%26Build" for r in focused), (
        "Focus must return only the sync-build family for 'how to sync and build'"
    )


# ---------------------------------------------------------------------------
# contamination_miss
# ---------------------------------------------------------------------------

def test_contamination_status_page_penalized_for_api_reference_query():
    """
    'where is the arkui api reference': a status/issues-tracker page titled
    'Idlize issues related to C-API' should receive a contamination penalty
    and rank below a canonical Documentation/reference page.
    """
    docs_page = _candidate(
        content="Official OpenHarmony documentation: docs.openharmony.cn. ArkUI API reference pages listed below.",
        source_path="wiki/Infrastructure/Documentation",
        doc_title="Documentation",
        section_title="Documentation",
        section_path="Infrastructure > Documentation",
        _query_field_best_coverage=0.4,
        _query_field_term_hits=2,
    )
    issues_page = _candidate(
        content="State on 2025/03/06. Extra AttributeModifier modifiers managed side, need remove from C-API.",
        source_path="wiki/C-API/Idlize-issues-related-to-C-API",
        doc_title="Idlize issues related to C-API",
        section_title="Idlize issues related to C-API",
        section_path="C-API > Idlize issues related to C-API",
        _query_field_best_coverage=0.1,
        _query_field_term_hits=1,
    )

    ordered = _order_candidates_by_canonicality(
        [issues_page, docs_page], query="where is the arkui api reference"
    )

    sources = [r["source_path"] for r in ordered]
    assert sources.index("wiki/Infrastructure/Documentation") < sources.index(
        "wiki/C-API/Idlize-issues-related-to-C-API"
    ), "Documentation page must rank above issues/status page for 'where is the api reference'"


def test_contamination_archive_page_penalized_for_setup_query():
    """
    'what host setup is recommended for development': archived/versioned pages
    should receive a contamination penalty and rank below the current setup page.
    """
    setup_page = _candidate(
        content="A Windows host is recommended for DevEco Studio. Use a Linux server for sync/build.",
        source_path="wiki/Infrastructure/Server-Setup-Linux",
        doc_title="Server Setup (Linux)",
        section_title="Recommended host setup",
        section_path="Infrastructure > Server Setup (Linux)",
        _query_field_best_coverage=0.45,
        _query_field_term_hits=3,
    )
    archive_page = _candidate(
        content="RRI Setup v130 — configure Linux host and install build tools.",
        source_path="wiki/Archive/Versions/v13x/RRI-Setup-v130",
        doc_title="RRI Setup v130",
        section_title="RRI Setup v130",
        section_path="Archive > Versions > v13x > RRI Setup v130",
        _query_field_best_coverage=0.2,
        _query_field_term_hits=1,
    )

    ordered = _order_candidates_by_canonicality(
        [archive_page, setup_page], query="what host setup is recommended for development"
    )

    sources = [r["source_path"] for r in ordered]
    assert sources.index("wiki/Infrastructure/Server-Setup-Linux") < sources.index(
        "wiki/Archive/Versions/v13x/RRI-Setup-v130"
    ), "Current setup page must rank above archived version page"


# ---------------------------------------------------------------------------
# navigation_miss
# ---------------------------------------------------------------------------

def test_navigation_c_api_overview_preferred_for_placement_query():
    """
    'where are UI interfaces and non-ui interfaces placed in c-api': the overview
    doc (which explicitly describes placement: 'UI interfaces' and 'non-ui interfaces'
    in section_title) must rank above a callbacks-guide page that only mentions
    callback lifecycle, not interface placement.

    Term analysis for query "where are UI interfaces and non-ui interfaces placed in c-api"
    (stop words removed, short tokens filtered):
    effective terms: "interfaces", "non", "placed", "api"
    - Overview section_title "UI interfaces and non-ui interfaces placement" → hits: interfaces, non, placed
    - Callbacks section_title "common info and regular case" → hits: none
    """
    # overview: section_title explicitly mentions "interfaces", "non-ui", "placement"
    overview = _candidate(
        content="UI subsystem interfaces are placed in Modifiers. Non-UI interfaces are placed in Accessors.",
        source_path="wiki/Features/C-API/C-API-Overview",
        doc_title="C-API Overview",
        section_title="UI interfaces and non-ui interfaces placement",
        section_path="Features > C-API > C-API Overview > UI interfaces and non-ui interfaces placement",
    )
    # callbacks page: no placement/interfaces terms in section title
    callbacks = _candidate(
        content="Callbacks are invoked by the framework. Interfaces for lifecycle control use hold/release pointers.",
        source_path="wiki/Features/C-API/C-API-Callbacks-guide",
        doc_title="C-API Callbacks guide",
        section_title="common info and regular case",
        section_path="Features > C-API > C-API Callbacks guide > common info",
    )

    ranked = _ranked_sources(
        "where are UI interfaces and non-ui interfaces placed in c-api",
        [callbacks, overview],
    )

    assert ranked[0] == "wiki/Features/C-API/C-API-Overview", (
        "C-API Overview must rank above Callbacks guide for placement query"
    )


# ---------------------------------------------------------------------------
# exact_lookup_procedural_miss
# ---------------------------------------------------------------------------

def test_exact_lookup_fix_rendering_preferred_for_white_screen_query():
    """
    'how to fix previewer white screen': the specific fix-rendering doc
    (which describes white_screen_003.diff and white_screen_004.diff patches)
    must rank above the generic SDK/previewer acquisition guide.
    """
    fix_doc = _candidate(
        content="Apply white_screen_003.diff in foundation/graphic/graphic_2d and white_screen_004.diff in foundation/arkui/ace_engine.",
        source_path="wiki/Previewer/Notes/Previewer-fix-rendering",
        doc_title="Previewer fix rendering",
        section_title="Fix white screen issue",
        section_path="Previewer > Notes > Previewer fix rendering",
    )
    sdk_guide = _candidate(
        content="Download and install the previewer from out/sdk/ohos-sdk/windows/previewer.",
        source_path="wiki/Infrastructure/How-to-get-previewer-and-SDK",
        doc_title="How to get previewer and SDK for ACE FW",
        section_title="How to add extra logging in sdk and obtain logs from previewer:",
        section_path="Infrastructure > How to get previewer and SDK",
    )

    ranked = _ranked_sources("how to fix previewer white screen", [sdk_guide, fix_doc])

    assert ranked[0] == "wiki/Previewer/Notes/Previewer-fix-rendering", (
        "Fix rendering doc must rank above SDK guide for 'how to fix previewer white screen'"
    )


# ---------------------------------------------------------------------------
# source_manifest_miss
# ---------------------------------------------------------------------------

def test_source_manifest_page_preferred_for_manifest_workflow_query():
    """
    'how to share feature manifest snapshot': the manifest workflow doc
    must rank above an e2e test page that only mentions manifests incidentally.
    """
    manifests_doc = _candidate(
        content="Run repo manifest -r -o default.xml, rewrite remotes for the feature branch, push to manifests repo.",
        source_path="wiki/Development/Working-with-manifests",
        doc_title="Working with manifests",
        section_title="Share feature manifest snapshot",
        section_path="Development > Working with manifests",
    )
    e2e_doc = _candidate(
        content="Build idlize: git clone ... npm run libarkts:reinstall:regenerate",
        source_path="wiki/Features/Test-generator/How-to-build-and-run-e2e-tests",
        doc_title="How to build and run e2e tests",
        section_title="How to build and run e2e tests",
        section_path="Features > Test generator > How to build and run e2e tests",
    )

    ranked = _ranked_sources(
        "how to share feature manifest snapshot", [e2e_doc, manifests_doc]
    )

    assert ranked[0] == "wiki/Development/Working-with-manifests", (
        "Manifests doc must rank above e2e test page for manifest workflow query"
    )


def test_source_manifest_page_preferred_for_new_developer_join_query():
    """
    'how new developer join feature branch using manifests': the manifest doc
    (with repo init command for feature branch) must rank above test pages
    that don't contain manifest initialization steps.
    """
    manifests_doc = _candidate(
        content="Initialize with repo init -u https://gitee.com/mazurdenis/manifests -b <branch-name> --no-repo-verify and sync.",
        source_path="wiki/Development/Working-with-manifests",
        doc_title="Working with manifests",
        section_title="Join feature branch",
        section_path="Development > Working with manifests",
    )
    callbacks_doc = _candidate(
        content="The CallbackKeeper provides lifecycle management for the own callbacks.",
        source_path="wiki/Features/C-API/C-API-Callbacks-guide",
        doc_title="C-API Callbacks guide",
        section_title="5.3.1 Common info",
        section_path="Features > C-API > C-API Callbacks guide",
    )

    ranked = _ranked_sources(
        "how new developer join feature branch using manifests",
        [callbacks_doc, manifests_doc],
    )

    assert ranked[0] == "wiki/Development/Working-with-manifests", (
        "Manifests doc must rank above C-API callbacks page for manifests workflow query"
    )
