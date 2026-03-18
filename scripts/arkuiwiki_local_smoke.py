#!/usr/bin/env python3
"""Profile wrapper for the generic wiki corpus local smoke runner."""

from scripts.wiki_corpus_local_smoke import main


if __name__ == "__main__":
    raise SystemExit(main(default_profile="arkuiwiki"))
