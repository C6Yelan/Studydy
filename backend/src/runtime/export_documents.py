"""已發布資料的離線文件；不呼叫模型、不修改生成內容。"""
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
import re
import zipfile

import pymupdf

from . import note_latex
from .note_content import CALLOUT_LABELS, body_html, numbered_heading, outline

FONT_DIR = Path('/usr/share/fonts/opentype/noto')
CSS = '''
@font-face { font-family: ExportCJK; src: url(NotoSansTC-Regular.otf); }
@font-face { font-family: ExportCJK; src: url(NotoSansTC-Bold.otf); font-weight: bold; }
body { font-family: ExportCJK; font-size: 12pt; line-height: 1.4; }
h1 { font-size: 18pt; } h2 { font-size: 14pt; margin: 8pt 0 4pt; }
p { margin: 0 0 6pt; white-space: pre-wrap; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 0 0 6pt; }
td, th { border: .5pt solid #555; padding: 3pt; }
h3 { font-size: 13pt; margin: 6pt 0 3pt; }
ul { margin: 0 0 6pt; padding-left: 14pt; } li { margin: 0 0 2pt; }
.caption, .callout-title { font-weight: bold; margin: 6pt 0 3pt; }
.formula { text-align: center; margin: 6pt 0; }
.formula td { border: 0; padding: 0; }
.callout { background-color: #f3f7fc; border-left: 3pt solid #2f6bff; padding: 4pt 8pt; margin: 6pt 0; }
.outline-2 { margin-left: 16pt; }
'''
PAGE = pymupdf.paper_rect('a4')
CONTENT = PAGE + (51.024, 51.024, -51.024, -51.024)


@lru_cache(maxsize=2)
def font_bytes(weight):
    from fontTools.ttLib import TTCollection
    collection = TTCollection(FONT_DIR / f'NotoSansCJK-{weight}.ttc')
    font = next(font for font in collection.fonts if font['name'].getDebugName(6) == f'NotoSansCJKtc-{weight}')
    data = BytesIO()
    font.save(data)
    collection.close()
    return data.getvalue()


def font_archive():
    archive = pymupdf.Archive()
    for weight in ('Regular', 'Bold'):
        archive.add((font_bytes(weight), f'NotoSansTC-{weight}.otf'))
    return archive


def pdf(html, images=(), *, keep_headings=False):
    archive = font_archive()
    for image in images:
        archive.add(image)
    forced_breaks = set()
    while True:
        story = pymupdf.Story(html=html, user_css=CSS, archive=archive)
        if not keep_headings:
            break
        pairs = []
        node = story.body.first_child
        while node is not None:
            following = node.next
            if node.tagname in ('h1', 'h2', 'h3') and following is not None and following.tagname in ('p', 'ul', 'table', 'div'):
                index = len(pairs)
                heading_id, body_id = f'note-heading-{index}', f'note-body-{index}'
                node.set_id(heading_id)
                following.set_id(body_id)
                pairs.append((heading_id, body_id))
                if index in forced_breaks:
                    node.set_attribute('style', 'page-break-before:always;')
            node = following
        # Story 不支援 break-after:avoid；先看實際分頁，將孤立標題移到正文開頭所在頁。
        # 只保留正文開頭，長段落與後續表格仍可自然跨頁。
        starts = {}
        def remember(position):
            if position.open_close & 1:
                starts[position.id] = position.page_num
        story.write(None, lambda _page, _filled: (PAGE, CONTENT, None), positionfn=remember)
        orphaned = {index for index, (heading, body) in enumerate(pairs) if starts[heading] != starts[body]}
        if not orphaned:
            story.reset()
            break
        forced_breaks.update(orphaned)
    stream = BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    more = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(CONTENT)
        story.draw(device)
        writer.end_page()
    writer.close()
    return stream.getvalue()


def source_text(e):
    unit = '段' if re.search(r'\.(mp3|m4a|wav|mp4|youtube)$', e.get('source_name') or '', re.I) else '頁'
    position = f'第 {e.get("normalized_page") or e["page"]} {unit}'
    return f'{e.get("source_name") or e.get("source_id") or "來源"} · {position}' + (
        f' · 區塊 {e["block_order"]}' if 'block_order' in e else '')


def md_text(value):
    return re.sub(r'([\\`*_{}\[\]<>#+.!|~-])', r'\\\1', str(value))


def _note_title(view):
    return '重點整理' if view['kind'] == 'summary' else '完整筆記'


def _source_notice(view):
    status = view['source_status']
    return status['quality'] == 'needs_review' or status['processing'] != 'succeeded' or status['decision'] != 'retain'


def _md_items(items, indent=''):
    lines = []
    for item in items:
        lines.append(f'{indent}- {md_text(item["text"])}')
        lines.extend(_md_items(item.get('children') or [], indent + '  '))
    return lines


def _md_table(headers, rows):
    def cell(value):
        return md_text(value).replace('\n', '<br>')
    return ['| ' + ' | '.join(cell(c) for c in headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |',
            *('| ' + ' | '.join(cell(c) for c in row) + ' |' for row in rows), '']


def _md_blocks(blocks):
    lines = []
    for block in blocks:
        kind = block['type']
        if kind == 'text':
            lines.extend([md_text(block['text']), ''])
        elif kind == 'bullets':
            lines.extend([*_md_items(block['items']), ''])
        elif kind == 'table':
            if block['caption']:
                lines.extend([f'**{md_text(block["caption"])}**', ''])
            lines.extend(_md_table(block['headers'], block['rows']))
        elif kind == 'formula':
            # 公式以 LaTeX 原樣輸出，不做 Markdown 跳脫。
            lines.extend(['$$', block['latex'], '$$', ''])
        else:
            title = CALLOUT_LABELS[block['style']] + (f'：{block["title"]}' if block['title'] else '')
            lines.extend([f'> **{md_text(title)}**', '>', *(f'> {line}' for line in _md_items(block['items'])), ''])
    return lines


def note_markdown(view):
    lines = [f'# {_note_title(view)}', '', f'教材：{view["material_id"]}', f'版本：{view["knowledge_structure_revision"]}', '']
    if _source_notice(view):
        lines += ['> 教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。', '']
    numbers = {o['paragraph_id']: o for o in outline(view['paragraphs'])}
    if numbers:
        lines.extend(['## 章節大綱', ''])
        lines.extend(f'{"  " if o["level"] == 2 else ""}- {md_text(numbered_heading(o))}' for o in numbers.values())
        lines.append('')

    def paragraph(p, number=None):
        if 'blocks' not in p:
            # 舊版段落格式：保留可讀，直到重新生成。
            lines.extend([f'## {md_text(p["heading"])}', '', md_text(p['text']), ''])
            if p.get('formula'):
                lines.extend([md_text(p['formula']), ''])
            if p.get('table'):
                lines.extend(_md_table(p['table']['headers'], p['table']['rows']))
            depth = 2
        else:
            depth = 1 + (number['level'] if number else p['level'])
            heading = numbered_heading(number) if number else p['heading']
            lines.extend([f'{"#" * depth} {md_text(heading)}', '', *_md_blocks(p['blocks'])])
        lines.extend(f'來源：{md_text(source_text(e))}  ' for e in p['evidence'])
        lines.append('')
        return depth

    for p in view['paragraphs']:
        depth = paragraph(p, numbers.get(p['id']))
        for a in view['annotations']:
            if a['paragraph_id'] == p['id']:
                lines.extend([f'{"#" * (depth + 1)} 個人註記', '', md_text(a['body']), ''])
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        lines.extend(['# 先前段落的註記', '', '以下註記未能精確對應目前段落，保留當時內容。', ''])
        for a in orphaned:
            lines.extend([f'原版本：{a["original_revision"]}', ''])
            depth = paragraph(a['original_paragraph'])
            lines.extend([f'{"#" * (depth + 1)} 個人註記', '', md_text(a['body']), ''])
    return '\n'.join(lines)


def note_pdf(view):
    # 正文與來源／註記分開；兩種筆記的正文都依內容自然分頁，不縮小字體。
    images = []
    body = pdf(body_html(view['paragraphs'], images), images, keep_headings=True)
    appendix, images = [f'<h1>{_note_title(view)}：來源與個人註記</h1>',
                f'<p>教材：{escape(view["material_id"])}\n版本：{escape(view["knowledge_structure_revision"])}</p>'], []
    if _source_notice(view):
        appendix.append('<p>教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。</p>')
    numbers = {o['paragraph_id']: numbered_heading(o) for o in outline(view['paragraphs'])}
    for p in view['paragraphs']:
        appendix.append(f'<h2>{escape(numbers.get(p["id"], p["heading"]))}</h2>')
        appendix.extend(f'<p>{escape(source_text(e))}</p>' for e in p['evidence'])
        appendix.extend(f'<p>個人註記：{escape(a["body"])}</p>' for a in view['annotations'] if a['paragraph_id'] == p['id'])
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        appendix.append('<h1>先前段落的註記</h1><p>以下註記未能精確對應目前段落，保留當時內容。</p>')
        for a in orphaned:
            appendix.append(f'<p>原版本：{escape(a["original_revision"])}</p>' + body_html([a['original_paragraph']], images, numbered=False))
            appendix.extend(f'<p>{escape(source_text(e))}</p>' for e in a['original_paragraph']['evidence'])
            appendix.append(f'<p>個人註記：{escape(a["body"])}</p>')
    with pymupdf.open(stream=body, filetype='pdf') as doc, pymupdf.open(stream=pdf(''.join(appendix), images, keep_headings=True), filetype='pdf') as extra:
        doc.insert_pdf(extra)
        return doc.tobytes(garbage=4, deflate=True)


def xml_text(value):
    # XML 1.0 允許 tab／換行；移除 Office 無法保存的控制字元與孤立 surrogate。
    return re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', value)


def _docx_items(container, items, depth=1):
    for item in items:
        container.add_paragraph(xml_text(item['text']), style='List Bullet' if depth == 1 else f'List Bullet {depth}')
        _docx_items(container, item.get('children') or [], depth + 1)


def _docx_table(doc, headers, rows):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = xml_text(value)
    for row in rows:
        for cell, value in zip(table.add_row().cells, row):
            cell.text = xml_text(value)
    # 相鄰表格之間須隔一段，否則 Word 會把它們合成同一個表格。
    doc.add_paragraph()


def _docx_blocks(doc, blocks):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    for block in blocks:
        kind = block['type']
        if kind == 'text':
            doc.add_paragraph(xml_text(block['text']))
        elif kind == 'bullets':
            _docx_items(doc, block['items'])
        elif kind == 'table':
            if block['caption']:
                doc.add_paragraph().add_run(xml_text(block['caption'])).bold = True
            _docx_table(doc, block['headers'], block['rows'])
        elif kind == 'formula':
            # Word 不能直接顯示 LaTeX；嵌入與 PDF 相同的排版圖，替代文字保留 LaTeX 原式。
            data, width, _ = note_latex.render_png(block['latex'])
            paragraph = doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            section = doc.sections[-1]
            page_width = section.page_width - section.left_margin - section.right_margin
            paragraph.add_run().add_picture(BytesIO(data), width=min(Pt(width), page_width))
            paragraph._p.xpath('.//wp:docPr')[0].set('descr', xml_text(block['latex']))
        else:
            box = doc.add_table(rows=1, cols=1)
            box.style = 'Table Grid'
            cell = box.rows[0].cells[0]
            shading = OxmlElement('w:shd')
            shading.set(qn('w:val'), 'clear')
            shading.set(qn('w:fill'), 'F3F7FC')
            cell._tc.get_or_add_tcPr().append(shading)
            title = CALLOUT_LABELS[block['style']] + (f'：{block["title"]}' if block['title'] else '')
            cell.paragraphs[0].add_run(xml_text(title)).bold = True
            _docx_items(cell, block['items'])
            doc.add_paragraph()


def note_docx(view):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    doc = Document()
    doc.core_properties.author = 'Studydy'
    doc.core_properties.last_modified_by = 'Studydy'
    doc.core_properties.comments = ''
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2', 'Heading 3'):
        style = doc.styles[name]
        style.font.name = 'Noto Sans CJK TC'
        fonts = style.element.get_or_add_rPr().find(qn('w:rFonts'))
        if fonts is None:
            fonts = OxmlElement('w:rFonts')
            style.element.get_or_add_rPr().append(fonts)
        fonts.set(qn('w:eastAsia'), 'Noto Sans CJK TC')
    doc.add_heading(_note_title(view), 0)
    doc.add_paragraph(f'教材：{view["material_id"]}\n版本：{view["knowledge_structure_revision"]}')
    if _source_notice(view):
        doc.add_paragraph('教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。')
    numbers = {o['paragraph_id']: o for o in outline(view['paragraphs'])}
    if numbers:
        doc.add_heading('章節大綱', 1)
        for o in numbers.values():
            item = doc.add_paragraph(xml_text(numbered_heading(o)))
            if o['level'] == 2:
                item.paragraph_format.left_indent = Pt(18)

    def paragraph(p, number=None):
        if 'blocks' not in p:
            # 舊版段落格式：保留可讀，直到重新生成。
            doc.add_heading(xml_text(p['heading']), 1)
            doc.add_paragraph(xml_text(p['text']))
            if p.get('formula'):
                doc.add_paragraph(xml_text(p['formula']))
            if p.get('table'):
                _docx_table(doc, p['table']['headers'], p['table']['rows'])
            level = 1
        else:
            level = number['level'] if number else p['level']
            doc.add_heading(xml_text(numbered_heading(number) if number else p['heading']), level)
            _docx_blocks(doc, p['blocks'])
        for e in p['evidence']:
            doc.add_paragraph(xml_text('來源：' + source_text(e)))
        return level

    for p in view['paragraphs']:
        level = paragraph(p, numbers.get(p['id']))
        for a in view['annotations']:
            if a['paragraph_id'] == p['id']:
                doc.add_heading('個人註記', level + 1)
                doc.add_paragraph(xml_text(a['body']))
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        doc.add_heading('先前段落的註記', 1)
        for a in orphaned:
            doc.add_paragraph(xml_text('原版本：' + a['original_revision']))
            level = paragraph(a['original_paragraph'])
            doc.add_heading('個人註記', level + 1)
            doc.add_paragraph(xml_text(a['body']))
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def zip_files(files):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return stream.getvalue()


def diagnosis_pdf(view):
    counts = {key: sum(len(item['points']) for item in view[key]) for key in ('strengths', 'weaknesses')}
    head = f'<h1>{"本組" if view["scope"] == "set" else "教材"}學習診斷</h1><p>優勢重點：{counts["strengths"]}　待加強重點：{counts["weaknesses"]}</p>'
    recommendations = view['recommendations']
    points = [(label, finding, point) for key, label in [('weaknesses', '弱點'), ('strengths', '優勢')]
              for finding in view[key] for point in finding['points']]
    selected_recommendations, selected = [], []

    def render():
        omitted = []
        if len(points) > len(selected):
            omitted.append(f'{len(points) - len(selected)} 個診斷重點')
        if len(recommendations) > len(selected_recommendations):
            omitted.append(f'{len(recommendations) - len(selected_recommendations)} 項複習建議')
        footer = f'<p>另有 {"、".join(omitted)}未列於摘要；完整內容請回到 Studydy 查看。</p>' if omitted else ''
        title = '<h2>建議先複習</h2>' if selected_recommendations else ''
        return pdf(head + title + ''.join(selected_recommendations) + ''.join(selected) + footer)

    def fits():
        with pymupdf.open(stream=render(), filetype='pdf') as doc:
            return len(doc) == 1

    for r in recommendations:
        selected_recommendations.append(f'<p>{escape(r["label"])}：{escape(r["reason"])}</p>')
        if not fits():
            selected_recommendations.pop()
            break
    for label, finding, point in points:
        selected.append(f'<h2>{label}：{escape(finding["label"])}</h2><p>{escape(point["title"])}\n答對 {point["correct_count"]} / {point["answer_count"]} 次</p>' +
            ''.join(f'<p>{escape(source_text(e))}</p>' for e in point['evidence']))
        if not fits():
            selected.pop()
            break
    return render()


def decode_png(value):
    from base64 import b64decode
    from struct import unpack
    from .errors import SourceError
    try:
        data = b64decode(value, validate=True)
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError()
        width, height = unpack('>II', data[16:24])
        if not 0 < width * height <= 16000000:
            raise ValueError()
        with pymupdf.open(stream=data, filetype='png') as document:
            document[0].get_pixmap()
        return data
    except Exception as error:
        raise SourceError('REQUEST_INVALID') from error


def compact_pdf(data):
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        # MuPDF 的原生 CFF 子集化對 Noto TC 會出現 Index bounds；採已安裝的 fontTools 實作。
        doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)
