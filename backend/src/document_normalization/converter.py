"""在受限子程序中將來源轉為 PDF，並核對輸出與來源對照。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import tempfile


MIME = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
}
MAX_FILE_BYTES = 100 * 1024 * 1024
POLICY = {
    'schema': 'normalization-policy/v1',
    'version': 1,
    'olefile': '0.47',
    'max_file_bytes': MAX_FILE_BYTES,
    'libreoffice': '26.2.5.2',
    'pymupdf': '1.28.0',
    'markdown_it': '3.0.0',
    'visible_slides_only': True,
    'notes': False,
    'remote_resources': False,
    'text_columns': 72,
}
_SAFE_ERRORS = {
    'UNSUPPORTED_MEDIA_TYPE', 'MATERIAL_TOO_LARGE', 'PDF_UNUSABLE', 'PDF_DAMAGED',
    'ZIP_PATH', 'OFFICE_ACTIVE_OR_ENCRYPTED', 'OFFICE_EXTERNAL_RELATIONSHIP',
    'OFFICE_TYPE_MISMATCH', 'OFFICE_INVALID_PACKAGE', 'UTF8_REQUIRED',
    'SLIDE_PAGE_COUNT_MISMATCH', 'UNSAFE_RENDER_HTML', 'NORMALIZER_VERSION_MISMATCH',
}


class NormalizationError(RuntimeError):
    pass


def configured_python():
    raw = os.environ.get('STUDYDY_NORMALIZER_PYTHON')
    if not raw:
        return None
    executable = Path(raw)
    if not executable.is_absolute() or not executable.is_file():
        raise NormalizationError('NORMALIZER_UNAVAILABLE')
    return executable


def conversion_policy():
    executable = configured_python()
    if executable is None:
        raise NormalizationError('NORMALIZER_UNAVAILABLE')
    fonts = subprocess.check_output(['fc-list', ':lang=zh', 'file', 'family'], text=True)
    font_lines = '\n'.join(sorted(fonts.splitlines()))
    return {**POLICY, 'fonts_sha256': hashlib.sha256(font_lines.encode()).hexdigest()}


def _limits():
    limits = (
        (resource.RLIMIT_AS, 2 * 1024**3),
        (resource.RLIMIT_FSIZE, MAX_FILE_BYTES),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_NPROC, 1024),
    )
    for kind, value in limits:
        resource.setrlimit(kind, (value, value))


def convert(data: bytes, extension: str, media_type: str, policy: dict) -> tuple[bytes, dict]:
    """只接受當前政策；子程序產物須對回原檔與輸出 PDF 的雜湊。"""
    if policy != conversion_policy():
        raise NormalizationError('NORMALIZER_VERSION_MISMATCH')
    if extension not in MIME or MIME[extension] != media_type:
        raise NormalizationError('UNSUPPORTED_MEDIA_TYPE')

    executable = configured_python()
    base = executable.resolve().parent.parent
    site = executable.parent.parent / 'lib/python3.12/site-packages'
    if not site.is_dir():
        raise NormalizationError('NORMALIZER_UNAVAILABLE')

    with tempfile.TemporaryDirectory(prefix='studydy-normalize-') as temporary:
        root = Path(temporary)
        source = root / 'input'
        output = root / 'output'
        source.mkdir()
        output.mkdir()
        (source / ('source' + extension)).write_bytes(data)

        # 子程序只讀所需 runtime／系統資源，僅 output 可寫；不繼承外部環境。
        command = [
            'bwrap', '--unshare-all', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', '/etc/fonts', '/etc/fonts',
            '--ro-bind', '/etc/libreoffice', '/etc/libreoffice',
            '--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--ro-bind', str(base), '/runtime',
            '--ro-bind', str(site), '/runtime/lib/python3.12/site-packages',
            '--ro-bind', str(Path(__file__).with_name('renderer.py')), '/renderer.py',
            '--ro-bind', str(source), '/input',
            '--bind', str(output), '/output',
            '--clearenv',
            '--setenv', 'HOME', '/tmp',
            '--setenv', 'PATH', '/usr/bin',
            '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'SAL_USE_VCLPLUGIN', 'svp',
            '--setenv', 'XDG_CACHE_HOME', '/tmp/cache',
            '/runtime/bin/python3.12', '/renderer.py',
            '/input/source' + extension, '/output', media_type,
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, preexec_fn=_limits,
        )
        try:
            stdout, _ = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise NormalizationError('NORMALIZATION_TIMEOUT') from None
        if process.returncode:
            reason = stdout.decode(errors='replace').strip()
            raise NormalizationError(reason if reason in _SAFE_ERRORS else 'NORMALIZATION_FAILED')

        try:
            pdf = (output / 'normalized.pdf').read_bytes()
            mapping = json.loads((output / 'mapping.json').read_text())
            if (
                not pdf or len(pdf) > MAX_FILE_BYTES
                or mapping['original_sha256'] != hashlib.sha256(data).hexdigest()
                or mapping['normalized_sha256'] != hashlib.sha256(pdf).hexdigest()
            ):
                raise ValueError
        except (OSError, KeyError, ValueError):
            raise NormalizationError('NORMALIZATION_FAILED') from None
        return pdf, mapping
