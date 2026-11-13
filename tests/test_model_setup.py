"""Setup model chỉ tải khi được yêu cầu và ghim version đã benchmark."""

from arxiv_pipeline.standardization import download_models


def test_default_setup_reuses_pinned_spacy_model(monkeypatch):
    monkeypatch.setattr(download_models, "version", lambda name: "3.8.0")
    calls = []
    monkeypatch.setattr(download_models.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    download_models.main([])
    assert not calls


def test_missing_model_setup_uses_official_pinned_wheel(monkeypatch):
    def missing(name):
        raise download_models.PackageNotFoundError(name)
    monkeypatch.setattr(download_models, "version", missing)
    calls = []
    monkeypatch.setattr(download_models.subprocess, "run", lambda cmd, **kwargs: calls.append((cmd, kwargs)))
    download_models.main([])
    assert calls == [([download_models.sys.executable, "-m", "pip", "install", download_models.SPACY_MODEL_URL], {"check": True})]
