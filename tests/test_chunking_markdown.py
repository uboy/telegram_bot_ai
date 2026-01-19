import unittest

from shared.document_loaders.chunking import split_markdown_section_into_chunks, split_text_structurally


class TestMarkdownChunking(unittest.TestCase):
    def test_fenced_code_block_not_split_in_markdown(self):
        code_lines = "
".join(["echo line" for _ in range(50)])
        text = "__CODE_BLOCK_START__
" + code_lines + "
__CODE_BLOCK_END__"
        chunks = split_markdown_section_into_chunks(text, max_chars=80, overlap=0)
        self.assertEqual(len(chunks), 1)
        self.assertIn("__CODE_BLOCK_START__", chunks[0])
        self.assertIn("__CODE_BLOCK_END__", chunks[0])
        self.assertIn("echo line", chunks[0])

    def test_fenced_code_block_not_split_in_structural(self):
        code_lines = "
".join(["cmd" for _ in range(60)])
        text = "intro
__CODE_BLOCK_START__
" + code_lines + "
__CODE_BLOCK_END__
footer"
        chunks = split_text_structurally(text, max_chars=80, overlap=0)
        # Should keep the code block intact as a single chunk
        joined = "
".join(chunks)
        self.assertIn("__CODE_BLOCK_START__", joined)
        self.assertIn("__CODE_BLOCK_END__", joined)
        self.assertIn("cmd", joined)


if __name__ == "__main__":
    unittest.main()
