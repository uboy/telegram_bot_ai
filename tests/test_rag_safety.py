import unittest

from shared.rag_safety import strip_unknown_citations, sanitize_commands_in_answer


class TestRagSafety(unittest.TestCase):
    def test_strip_unknown_citations(self):
        context = "SOURCE_ID: doc1
CONTENT: test"
        answer = "A [source_id]doc1] B [source_id]doc2]"
        cleaned = strip_unknown_citations(answer, context)
        self.assertIn("[source_id]doc1]", cleaned)
        self.assertNotIn("doc2", cleaned)

    def test_sanitize_commands_in_answer_filters_unknown(self):
        context = "repo sync -c -j 8
./build.sh --product-name rk3568 --ccache"
        answer = (
            "```bash
"
            "repo sync -c -j 8
"
            "repo sync -c -j 99
"
            "./build.sh --product-name rk3568 --ccache
"
            "```"
        )
        cleaned = sanitize_commands_in_answer(answer, context)
        self.assertIn("repo sync -c -j 8", cleaned)
        self.assertIn("./build.sh --product-name rk3568 --ccache", cleaned)
        self.assertNotIn("-j 99", cleaned)

    def test_sanitize_commands_in_answer_filters_wiki_urls(self):
        context = "repo sync -c -j 8"
        answer = "```bash
repo sync -c -j 8
https://example.com/wikis/Sync&Build
```"
        cleaned = sanitize_commands_in_answer(answer, context)
        self.assertNotIn("/wikis/", cleaned)


if __name__ == "__main__":
    unittest.main()
