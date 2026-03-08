import unittest

from shared.rag_safety import sanitize_commands_in_answer


class TestRagSafetyTokenGrounding(unittest.TestCase):
    def test_preserves_grounded_command_with_prompt_prefix_and_compact_option(self):
        context = "repo sync -c -j 8"
        answer = "```bash\n$ repo sync -c -j8\n```"

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertIn("repo sync -c -j8", cleaned)
        self.assertNotIn("Команда отсутствует", cleaned)

    def test_preserves_grounded_subset_of_context_command(self):
        context = "./build.sh --product-name rk3568 --ccache --jobs 8"
        answer = "Use `./build.sh --product-name rk3568 --ccache` to build."

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertIn("`./build.sh --product-name rk3568 --ccache`", cleaned)
        self.assertNotIn("команда отсутствует", cleaned)

    def test_rejects_invented_option_even_when_command_signature_matches(self):
        context = "repo sync -c -j 8"
        answer = "```bash\nrepo sync -c -j 8 --force-sync\n```"

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("--force-sync", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_rejects_different_command_value_under_same_signature(self):
        context = "python build.py --mode debug"
        answer = "Run `python build.py --mode release` next."

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("--mode release", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_rejects_swapped_option_values_even_with_same_token_bag(self):
        context = "python build.py --src input --dst output"
        answer = "```bash\npython build.py --src output --dst input\n```"

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("--src output", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_rejects_dropped_option_marker_with_reused_values(self):
        context = "python build.py --src input --dst output"
        answer = "Use `python build.py input output` only after configure."

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("`python build.py input output`", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_rejects_non_allowlisted_command_family_without_grounding(self):
        context = "repo sync -c -j 8"
        answer = "```bash\ntar -c -z -f app.tar.gz src\n```"

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("tar -c -z -f app.tar.gz src", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_rejects_two_token_command_family_without_grounding(self):
        context = "repo sync -c -j 8"
        answer = "Use `pytest tests/test_rag_safety.py` to validate."

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertNotIn("`pytest tests/test_rag_safety.py`", cleaned)
        self.assertIn("В найденных источниках нет точных команд", cleaned)

    def test_preserves_grounded_segment_from_chained_context_command(self):
        context = "cd out && make test"
        answer = "```bash\nmake test\n```"

        cleaned = sanitize_commands_in_answer(answer, context)

        self.assertIn("make test", cleaned)
        self.assertNotIn("Команда отсутствует", cleaned)


if __name__ == "__main__":
    unittest.main()
