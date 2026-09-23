"""只在隔離子程序中解析原檔；不連線、不匯入產品 DB／模型。"""

from collections import Counter
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

import markdown_it
from markdown_it import MarkdownIt
import olefile
import pymupdf


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
MAX_FILE_BYTES = 100 * 1024 * 1024
# 子程序不匯入 converter；格式契約在隔離邊界再次核對。
MIME = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
}
_STORY_CSS = (
    'body {font-family:sans-serif;font-size:11pt;} '
    'p {margin:0 0 6pt;} '
    '.plain p {white-space:pre-wrap;font-family:monospace;} '
    'pre {white-space:pre-wrap;} '
    'td,th {border:1px solid #aaa;padding:4pt;}'
)
_ACTIVE_PARTS = {
    'vba', 'macros', 'objectpool', 'activex', 'encryptedpackage',
    'encryptioninfo', 'encryptedsummary',
}
_ACTIVE_CONTENT_TYPES = (b'macroenabled', b'vbaproject', b'activex', b'oleobject')
_OFFICE_EXPORT_OPTIONS = (
    'ExportHiddenSlides', 'ExportNotes', 'ExportNotesPages',
    'ExportOnlyNotesPages', 'ExportFormFields',
)
_SAFE_ERRORS = {
    'UNSUPPORTED_MEDIA_TYPE', 'MATERIAL_TOO_LARGE', 'PDF_UNUSABLE', 'PDF_DAMAGED',
    'ZIP_PATH', 'OFFICE_ACTIVE_OR_ENCRYPTED', 'OFFICE_EXTERNAL_RELATIONSHIP',
    'OFFICE_TYPE_MISMATCH', 'OFFICE_INVALID_PACKAGE', 'UTF8_REQUIRED',
    'SLIDE_PAGE_COUNT_MISMATCH', 'UNSAFE_RENDER_HTML', 'NORMALIZER_VERSION_MISMATCH',
}


def wrap_text(line):
    parts = []
    current = ''
    width = 0
    for char in line:
        advance = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if width + advance > 72:
            parts.append(current)
            current = ''
            width = 0
        current += char
        width += advance
    return [*parts, current]


def validate(source):
    """拒絕損毀、加密、主動內容及外部關聯，再交給轉檔器。"""
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('MATERIAL_TOO_LARGE')

    extension = source.suffix.lower()
    if extension == '.pdf':
        try:
            with pymupdf.open(source) as document:
                if not document.is_pdf or document.needs_pass or not document.page_count:
                    raise ValueError('PDF_UNUSABLE')
        except pymupdf.FileDataError:
            raise ValueError('PDF_DAMAGED') from None

    elif extension in ('.doc', '.ppt'):
        try:
            if not olefile.isOleFile(source):
                raise ValueError('OFFICE_INVALID_PACKAGE')
            with olefile.OleFileIO(source, raise_defects=olefile.DEFECT_INCORRECT) as compound:
                parts = {
                    part.casefold()
                    for path in compound.listdir(streams=True, storages=True)
                    for part in path
                }
                if any(
                    part in _ACTIVE_PARTS or part.startswith('_vba_project')
                    for part in parts
                ):
                    raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                required = 'WordDocument' if extension == '.doc' else 'PowerPoint Document'
                if not compound.exists(required):
                    raise ValueError('OFFICE_TYPE_MISMATCH')
        except OSError:
            raise ValueError('OFFICE_INVALID_PACKAGE') from None

    elif extension in ('.docx', '.pptx'):
        try:
            with zipfile.ZipFile(source) as archive:
                for entry in archive.infolist():
                    entry_path = PurePosixPath(entry.filename)
                    if (
                        entry_path.is_absolute() or '..' in entry_path.parts
                        or '\\' in entry.filename
                    ):
                        raise ValueError('ZIP_PATH')
                    if (
                        entry.flag_bits & 1
                        or 'vbaproject' in entry.filename.lower()
                        or '/embeddings/' in entry.filename.lower()
                        or '/activex/' in entry.filename.lower()
                    ):
                        raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                    if entry.filename.endswith('.rels'):
                        root = ET.fromstring(archive.read(entry))
                        if any(
                            relationship.get('TargetMode') == 'External'
                            for relationship in root
                        ):
                            raise ValueError('OFFICE_EXTERNAL_RELATIONSHIP')

                if '[Content_Types].xml' not in archive.namelist():
                    raise ValueError('OFFICE_INVALID_PACKAGE')
                content_types = archive.read('[Content_Types].xml').lower()
                if any(marker in content_types for marker in _ACTIVE_CONTENT_TYPES):
                    raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                main_part = (
                    'word/document.xml' if extension == '.docx'
                    else 'ppt/presentation.xml'
                )
                if main_part not in archive.namelist():
                    raise ValueError('OFFICE_TYPE_MISMATCH')
        except zipfile.BadZipFile:
            raise ValueError('OFFICE_INVALID_PACKAGE') from None

    elif extension in ('.txt', '.md'):
        try:
            source.read_text(encoding='utf-8-sig')
        except UnicodeError:
            raise ValueError('UTF8_REQUIRED') from None

    else:
        raise ValueError('UNSUPPORTED_MEDIA_TYPE')


def text_html(source):
    """建立可繪製的文字，並保留原始行或 Markdown 區塊位置。"""
    text = source.read_text(encoding='utf-8-sig')
    anchors = {}
    if source.suffix == '.txt':
        elements = []
        for number, line in enumerate(text.splitlines(), 1):
            key = f'line-{number}'
            anchors[key] = {'line_start': number, 'line_end': number}
            content = '<br/>'.join(
                html.escape(part) for part in wrap_text(line.expandtabs(4))
            ) or '&#160;'
            elements.append(f'<p id="{key}">{content}</p>')
        return '<div class="plain">' + ''.join(elements) + '</div>', anchors

    parser = MarkdownIt('commonmark', {'html': False, 'linkify': False}).enable('table')
    tokens = parser.parse(text)
    for number, token in enumerate(tokens):
        if token.map and token.type not in ['inline']:
            key = f'block-{number}'
            anchors[key] = {'line_start': token.map[0] + 1, 'line_end': token.map[1]}
            token.attrSet('id', key)
        if token.children:
            for child in token.children:
                if child.type == 'image':
                    child.type = 'text'
                    child.tag = ''
                    child.content = '[圖片未載入]'
                    child.children = None
                    child.attrs = {}
                elif child.type in ['link_open', 'link_close']:
                    child.tag = 'span'
                    child.attrs = {}

    # fenced code renderer 原生不保留 token attrs，明確加入 source block id。
    fence = parser.renderer.rules['fence']

    def fenced(tokens, index, options, environment):
        rendered = fence(tokens, index, options, environment)
        block_id = tokens[index].attrGet('id')
        return rendered.replace('<pre>', f'<pre id="{block_id}">', 1)

    parser.renderer.rules['fence'] = fenced
    return parser.renderer.render(tokens, parser.options, {}), anchors


def story_pdf(source, target):
    """將安全文字排成 PDF，記下原始區塊落在哪一頁與區域。"""
    markup, anchors = text_html(source)
    if any(fragment in markup for fragment in ('<img', '<script', 'href=', 'src=')):
        raise ValueError('UNSAFE_RENDER_HTML')

    story = pymupdf.Story(markup, user_css=_STORY_CSS)
    records = []
    writer = pymupdf.DocumentWriter(str(target))
    try:
        page = 0
        while True:
            page += 1
            device = writer.begin_page(pymupdf.Rect(0, 0, 595, 842))
            more, _ = story.place(pymupdf.Rect(40, 40, 555, 802))

            def position(item):
                if item.id in anchors and item.open_close & 1:
                    records.append({
                        'normalized_page': page,
                        'region': list(item.rect),
                        'origin_locator': anchors[item.id],
                        'accuracy': 'exact',
                        'anchor': item.id,
                    })

            story.element_positions(position)
            story.draw(device)
            writer.end_page()
            if not more:
                break
    finally:
        writer.close()
    return records


def docx_mapping(source, document):
    """只有段落文字在 PDF 中唯一命中時，才標記精確來源位置。"""
    with zipfile.ZipFile(source) as archive:
        tree = ET.fromstring(archive.read('word/document.xml'))
    texts = [''.join(paragraph.itertext()) for paragraph in tree.iter(f'{{{W}}}p')]
    counts = Counter(texts)
    records = []
    for number, text in enumerate(texts, 1):
        hits = [
            {'normalized_page': page_number + 1, 'region': list(region)}
            for page_number, page in enumerate(document)
            for region in page.search_for(text)
        ] if text else []
        status = (
            'exact' if counts[text] == 1 and len(hits) == 1
            else 'ambiguous' if hits else 'unavailable'
        )
        reason = (
            'unique_text_match' if status == 'exact'
            else 'repeated_or_split_text' if hits else 'no_exact_text_match'
        )
        records.append({
            'origin_locator': {'document_part': 'word/document.xml', 'paragraph': number},
            'accuracy': status,
            'reason': reason,
            'candidates': hits,
        })
    return records


def pptx_mapping(source, document):
    """只對可見投影片建立頁碼對照，拒絕轉檔後頁數不符。"""
    with zipfile.ZipFile(source) as archive:
        tree = ET.fromstring(archive.read('ppt/presentation.xml'))
        relationships = {
            item.get('Id'): item.get('Target')
            for item in ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        }
        slides = []
        for number, item in enumerate(tree.find(f'{{{P}}}sldIdLst'), 1):
            relationship_id = item.get(
                '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
            )
            target = relationships[relationship_id]
            root = ET.fromstring(archive.read('ppt/' + target))
            slides.append({
                'original_slide_number': number,
                'slide_id': item.get('id'),
                'hidden': root.get('show') == '0',
            })
    visible = [slide for slide in slides if not slide['hidden']]
    if len(visible) != len(document):
        raise ValueError('SLIDE_PAGE_COUNT_MISMATCH')
    return [
        {'normalized_page': number + 1, 'origin_locator': slide, 'accuracy': 'exact'}
        for number, slide in enumerate(visible)
    ]


def main():
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    media_type = sys.argv[3]
    if source.suffix not in MIME or MIME[source.suffix] != media_type:
        raise ValueError('UNSUPPORTED_MEDIA_TYPE')
    if (
        pymupdf.VersionBind != '1.28.0'
        or markdown_it.__version__ != '3.0.0'
        or olefile.__version__ != '0.47'
    ):
        raise ValueError('NORMALIZER_VERSION_MISMATCH')

    validate(source)
    target = output / 'normalized.pdf'
    extension = source.suffix
    if extension == '.pdf':
        target.write_bytes(source.read_bytes())
    elif extension in ('.docx', '.pptx', '.doc', '.ppt'):
        version = subprocess.check_output(
            ['/usr/lib/libreoffice/program/soffice', '--version'], text=True,
        )
        if '26.2.5.2' not in version:
            raise ValueError('NORMALIZER_VERSION_MISMATCH')
        options = {
            key: {'type': 'boolean', 'value': 'false'}
            for key in _OFFICE_EXPORT_OPTIONS
        }
        export = (
            'impress_pdf_Export' if extension in ('.pptx', '.ppt')
            else 'writer_pdf_Export'
        )
        # 獨立 profile 無可信任文件目錄，最高巨集安全層級不執行文件巨集。
        profile = Path('/tmp/profile/user')
        profile.mkdir(parents=True, exist_ok=True)
        (profile / 'registrymodifications.xcu').write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value>'
            '</prop></item></oor:items>'
        )
        completed = subprocess.run(
            [
                '/usr/lib/libreoffice/program/soffice',
                '-env:UserInstallation=file:///tmp/profile',
                '--headless', '--nologo', '--nodefault', '--norestore',
                '--convert-to', 'pdf:' + export + ':' + json.dumps(options),
                '--outdir', str(output), str(source),
            ],
            capture_output=True,
        )
        generated = output / (source.stem + '.pdf')
        if completed.returncode or not generated.is_file():
            raise ValueError('NORMALIZATION_FAILED')
        generated.rename(target)
    else:
        records = story_pdf(source, target)

    validate(target)
    with pymupdf.open(target) as document:
        if extension == '.pdf':
            records = [
                {
                    'normalized_page': number + 1,
                    'origin_locator': {'original_page': number + 1},
                    'accuracy': 'exact',
                }
                for number in range(len(document))
            ]
        elif extension == '.docx':
            records = docx_mapping(source, document)
        elif extension == '.pptx':
            records = pptx_mapping(source, document)
        elif extension in ('.doc', '.ppt'):
            # 舊二進位格式不假造原始段落／投影片位置，保留 normalized PDF 頁與原檔下載。
            records = []
        mapping = {
            'schema': 'source-mapping/v1',
            'format': extension[1:],
            'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'normalized_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'page_count': len(document),
            'records': records,
            'quality_notice': 'PDF 優先；自動轉換品質不保證。',
        }
    (output / 'mapping.json').write_text(
        json.dumps(mapping, ensure_ascii=False, separators=(',', ':'))
    )


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if str(error) in _SAFE_ERRORS else 'NORMALIZATION_FAILED')
        raise SystemExit(1)
