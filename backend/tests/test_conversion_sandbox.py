"""真正的轉檔 launcher 必須隔離父程序檔案、環境及網路。"""

from pathlib import Path
import socket

from document_normalization import converter


def test_renderer_cannot_access_parent_files_environment_or_loopback(tmp_path, monkeypatch):
    private = tmp_path / 'parent-only.txt'
    private.write_text('synthetic private marker')
    renderer = Path(converter.__file__).with_name('renderer.py').read_text()
    monkeypatch.setenv('SYNTHETIC_PRIVATE_VALUE', 'must-not-enter-renderer')
    observed = []
    original = converter.subprocess.Popen

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        # 探針檢查邊界後執行原 renderer，仍須產生真正 PDF 與通過 hash 驗證。
        (tmp_path / 'renderer.py').write_text(
            'import os, socket, runpy\nfrom pathlib import Path\n'
            f'assert not Path({str(private)!r}).exists()\n'
            "assert os.environ.get('SYNTHETIC_PRIVATE_VALUE') is None\n"
            f"try:\n socket.create_connection(('127.0.0.1', {port}), timeout=.5)\n"
            "except OSError: pass\nelse: raise AssertionError('parent network visible')\n"
            "print('ISOLATION_OK', flush=True)\n"
            "runpy.run_path('/input/renderer_impl.py', run_name='__main__')\n"
        )
        monkeypatch.setattr(converter, '__file__', str(tmp_path / 'converter.py'))

        def spawn(command, **kwargs):
            if command[0] != 'bwrap':
                return original(command, **kwargs)
            source = Path(command[command.index('/input') - 1])
            (source / 'renderer_impl.py').write_text(renderer)
            process = original(command, **kwargs)
            communicate = process.communicate

            def checked(*args, **options):
                stdout, stderr = communicate(*args, **options)
                observed.append(stdout.startswith(b'ISOLATION_OK'))
                return stdout, stderr

            process.communicate = checked
            return process

        monkeypatch.setattr(converter.subprocess, 'Popen', spawn)
        pdf, mapping = converter.convert(
            b'Synthetic sandbox source.', '.txt', 'text/plain', converter.conversion_policy(),
        )
    assert observed == [True]
    assert pdf.startswith(b'%PDF') and mapping['page_count'] == 1
