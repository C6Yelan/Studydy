import pytest

from studydy_local_ai.download import download_ocr


def test_model_download_pins_revision_resumes_and_never_overwrites_unknown_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    revision = 'a' * 40
    calls = []
    monkeypatch.setitem(__import__('sys').modules, 'huggingface_hub', SimpleNamespace(
        snapshot_download=lambda repo, **kwargs: calls.append((repo, kwargs['revision'])),
    ))
    download_ocr(tmp_path, revision)
    download_ocr(tmp_path, revision)
    assert calls == [('baidu/Unlimited-OCR', revision)] * 2
    (tmp_path / '.studydy-revision').write_text('b' * 40)
    with pytest.raises(ValueError, match='OCR_TARGET_NOT_EMPTY'):
        download_ocr(tmp_path, revision)
    (tmp_path / '.studydy-revision').unlink()
    saved = tmp_path / 'config.json'
    saved.write_text('existing model')
    with pytest.raises(ValueError, match='OCR_TARGET_NOT_EMPTY'):
        download_ocr(tmp_path, revision)
    assert saved.read_text() == 'existing model'
    assert len(calls) == 2
