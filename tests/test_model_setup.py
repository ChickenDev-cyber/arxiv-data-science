"""Setup model chỉ tải khi được yêu cầu và ghim version đã benchmark."""

import unittest
from unittest.mock import patch
from arxiv_pipeline.standardization import download_models


class TestModelSetup(unittest.TestCase):
    def test_default_setup_reuses_pinned_spacy_model(self):
        calls = []
        with patch.object(download_models, "version", return_value="3.8.0"), \
             patch.object(download_models.subprocess, "run", side_effect=lambda *args, **kwargs: calls.append(args)):
            download_models.main([])
            self.assertFalse(calls)

    def test_missing_model_setup_uses_official_pinned_wheel(self):
        calls = []

        def missing(name):
            raise download_models.PackageNotFoundError(name)

        with patch.object(download_models, "version", side_effect=missing), \
             patch.object(download_models.subprocess, "run", side_effect=lambda cmd, **kwargs: calls.append((cmd, kwargs))):
            download_models.main([])
            self.assertEqual(
                calls,
                [([download_models.sys.executable, "-m", "pip", "install", download_models.SPACY_MODEL_URL], {"check": True})],
            )


if __name__ == "__main__":
    unittest.main()
