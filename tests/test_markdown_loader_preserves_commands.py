import tempfile
import unittest
from pathlib import Path

from shared.document_loaders.markdown_loader import MarkdownLoader


class TestMarkdownLoaderPreservesCommands(unittest.TestCase):
    def test_preserves_underscores_and_fenced_code(self) -> None:
        markdown_text = (
            "# Initialize repository and sync code\n"
            "Use project directories like ohos_master and ohos_weekly.\n"
            "Link prebuilts: `ln -s /data/shared/openharmony_prebuilts openharmony_prebuilts`\n"
            "Mirror URL: https://gitcode.com/openharmony/arkui_ace_engine/overview\n"
            "\n"
            "```bash\n"
            "repo init -u https://gitee.com/openharmony/manifest.git -b OpenHarmony_ArkUI_Upstream_2024\n"
            "repo sync -c -j 8\n"
            "```\n"
            "1. Build block inside list:\n"
            "   ```\n"
            "   ./build.sh --product-name rk3568 --ccache\n"
            "   ```\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "Sync&Build.md"
            path.write_text(markdown_text, encoding="utf-8")

            loader = MarkdownLoader()
            chunks = loader.load(str(path), options={"chunking_mode": "full"})

        self.assertEqual(len(chunks), 1)
        content = chunks[0]["content"]

        self.assertIn("ohos_master", content)
        self.assertIn("ohos_weekly", content)
        self.assertIn("openharmony_prebuilts", content)
        self.assertIn("arkui_ace_engine", content)
        self.assertIn("OpenHarmony_ArkUI_Upstream_2024", content)
        self.assertIn("```", content)
        self.assertIn("repo init", content)
        self.assertIn("repo sync -c -j 8", content)
        self.assertIn("./build.sh --product-name rk3568 --ccache", content)


if __name__ == "__main__":
    unittest.main()
