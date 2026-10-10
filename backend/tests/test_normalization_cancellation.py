"""可取消的轉檔不以原 60 秒期限殺掉正常工作。"""
import subprocess
import sys
from pathlib import Path
import pytest
from document_normalization import converter
from test_podcast_process import alive


def test_conversion_waits_for_process_and_kills_it_when_source_is_cancelled(monkeypatch):
    monkeypatch.setattr(converter, 'conversion_policy', lambda: {})
    original = subprocess.Popen
    children = []
    checks = 0
    def spawn(*args, **kwargs):
        process = original([sys.executable, '-c', 'import time;time.sleep(60)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        children.append(process)
        return process
    def check():
        nonlocal checks
        checks += 1
        if checks == 4: raise converter.NormalizationError('SOURCE_CANCELLED')
    monkeypatch.setattr(converter.subprocess, 'Popen', spawn)
    with pytest.raises(converter.NormalizationError, match='SOURCE_CANCELLED'):
        converter.convert(b'synthetic', '.txt', converter.MIME['.txt'], {}, cancellation_check=check)
    assert checks == 4 and len(children) == 1 and not alive(children[0].pid)
