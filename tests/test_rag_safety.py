import unittest

from shared.rag_safety import strip_unknown_citations, strip_untrusted_urls, sanitize_commands_in_answer


class TestRagSafety(unittest.TestCase):
    def test_strip_unknown_citations(self):
        context = "SOURCE_ID: doc1\nCONTENT: test"
        answer = "A [source_id]doc1] B [source_id]doc2]"
        cleaned = strip_unknown_citations(answer, context)
        self.assertIn("[source_id]doc1]", cleaned)
        self.assertNotIn("doc2", cleaned)

    def test_strip_untrusted_urls(self):
        context = "repo sync -c -j 8"
        answer = "See [guide](https://example.com/guide) and https://example.com/raw"
        cleaned = strip_untrusted_urls(answer, context)
        self.assertNotIn("https://example.com/guide", cleaned)
        self.assertNotIn("https://example.com/raw", cleaned)

    def test_sanitize_commands_in_answer_filters_unknown(self):
        context = "repo sync -c -j 8\n./build.sh --product-name rk3568 --ccache"
        answer = (
            "```bash\n"
            "repo sync -c -j 8\n"
            "repo sync -c -j 99\n"
            "./build.sh --product-name rk3568 --ccache\n"
            "```"
        )
        cleaned = sanitize_commands_in_answer(answer, context)
        self.assertIn("repo sync -c -j 8", cleaned)
        self.assertIn("./build.sh --product-name rk3568 --ccache", cleaned)
        self.assertNotIn("-j 99", cleaned)

    def test_sanitize_commands_in_answer_filters_wiki_urls(self):
        context = "repo sync -c -j 8"
        answer = "```bash\nrepo sync -c -j 8\nhttps://example.com/wikis/Sync&Build\n```"
        cleaned = sanitize_commands_in_answer(answer, context)
        self.assertNotIn("/wikis/", cleaned)


if __name__ == "__main__":
    unittest.main()
