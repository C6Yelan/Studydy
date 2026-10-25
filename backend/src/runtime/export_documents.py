"""已發布資料的離線文件；不呼叫模型、不修改生成內容。"""
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
import re
import zipfile

import pymupdf

FONT_DIR = Path('/usr/share/fonts/opentype/noto')
CSS = '''
@font-face { font-family: ExportCJK; src: url(NotoSansTC-Regular.otf); }
@font-face { font-family: ExportCJK; src: url(NotoSansTC-Bold.otf); font-weight: bold; }
body { font-family: ExportCJK; font-size: 12pt; line-height: 1.4; }
h1 { font-size: 18pt; } h2 { font-size: 14pt; margin: 8pt 0 4pt; }
p { margin: 0 0 6pt; white-space: pre-wrap; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; }
td, th { border: .5pt solid #555; padding: 3pt; }
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


def summary_with_bold_headings(paragraphs):
    from .note_content import summary_pdf
    # 先使用生成時的一頁排版；只替換 h2 的中文後備字形，不重新換行或移動正文。
    with pymupdf.open(stream=summary_pdf(paragraphs), filetype='pdf') as doc:
        page = doc[0]
        headings = [span for block in page.get_text('dict')['blocks']
                    for line in block.get('lines', []) for span in line['spans']
                    if span['size'] == 14 and span['font'].startswith('Droid Sans Fallback')]
        if headings:
            for span in headings:
                # 向內留微小邊界，避免移除緊鄰的西文字元；圖形、表格和正文全部保留。
                page.add_redact_annot(pymupdf.Rect(span['bbox']) + (.01, .01, -.01, -.01), fill=None)
            page.apply_redactions(images=0, graphics=0)
            page.insert_font(fontname='SummaryCJKHeading', fontbuffer=font_bytes('Bold'))
            for span in headings:
                page.insert_text(span['origin'], span['text'], fontsize=span['size'], fontname='SummaryCJKHeading')
        return doc.tobytes(garbage=4, deflate=True)


def pdf(html):
    story = pymupdf.Story(html=html, user_css=CSS, archive=font_archive())
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


def note_markdown(view):
    title = '重點整理' if view['kind'] == 'summary' else '完整筆記'
    lines = [f'# {title}', '', f'教材：{view["material_id"]}', f'版本：{view["knowledge_structure_revision"]}', '']
    status = view['source_status']
    if status['quality'] == 'needs_review' or status['processing'] != 'succeeded' or status['decision'] != 'retain':
        lines += ['> 教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。', '']

    def paragraph(p):
        lines.extend([f'## {md_text(p["heading"])}', '', md_text(p['text']), ''])
        if p.get('formula'):
            lines.extend([md_text(p['formula']), ''])
        if p.get('table'):
            table = p['table']
            lines.append('| ' + ' | '.join(md_text(c).replace('\n', '<br>') for c in table['headers']) + ' |')
            lines.append('| ' + ' | '.join('---' for _ in table['headers']) + ' |')
            lines.extend('| ' + ' | '.join(md_text(c).replace('\n', '<br>') for c in row) + ' |' for row in table['rows'])
            lines.append('')
        lines.extend(f'- 來源：{md_text(source_text(e))}' for e in p['evidence'])
        lines.append('')

    for p in view['paragraphs']:
        paragraph(p)
        for a in view['annotations']:
            if a['paragraph_id'] == p['id']:
                lines.extend(['### 個人註記', '', md_text(a['body']), ''])
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        lines.extend(['# 先前段落的註記', '', '以下註記未能精確對應目前段落，保留當時內容。', ''])
        for a in orphaned:
            lines.extend([f'原版本：{a["original_revision"]}', ''])
            paragraph(a['original_paragraph'])
            lines.extend(['### 個人註記', '', md_text(a['body']), ''])
    return '\n'.join(lines)


def note_pdf(view):
    from .note_content import body_html
    # 正文與來源／註記分開，摘要不因附錄增加而縮小字體。
    body = summary_with_bold_headings(view['paragraphs']) if view['kind'] == 'summary' else pdf(body_html(view['paragraphs']))
    appendix = [f'<h1>{"重點整理" if view["kind"] == "summary" else "完整筆記"}：來源與個人註記</h1>',
                f'<p>教材：{escape(view["material_id"])}\n版本：{escape(view["knowledge_structure_revision"])}</p>']
    status = view['source_status']
    if status['quality'] == 'needs_review' or status['processing'] != 'succeeded' or status['decision'] != 'retain':
        appendix.append('<p>教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。</p>')
    for p in view['paragraphs']:
        appendix.append(f'<h2>{escape(p["heading"])}</h2>')
        appendix.extend(f'<p>{escape(source_text(e))}</p>' for e in p['evidence'])
        appendix.extend(f'<p>個人註記：{escape(a["body"])}</p>' for a in view['annotations'] if a['paragraph_id'] == p['id'])
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        appendix.append('<h1>先前段落的註記</h1><p>以下註記未能精確對應目前段落，保留當時內容。</p>')
        for a in orphaned:
            appendix.append(f'<p>原版本：{escape(a["original_revision"])}</p>' + body_html([a['original_paragraph']]))
            appendix.extend(f'<p>{escape(source_text(e))}</p>' for e in a['original_paragraph']['evidence'])
            appendix.append(f'<p>個人註記：{escape(a["body"])}</p>')
    with pymupdf.open(stream=body, filetype='pdf') as doc, pymupdf.open(stream=pdf(''.join(appendix)), filetype='pdf') as extra:
        doc.insert_pdf(extra)
        return doc.tobytes(garbage=4, deflate=True)


def xml_text(value):
    # XML 1.0 允許 tab／換行；移除 Office 無法保存的控制字元與孤立 surrogate。
    return re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', value)


def note_docx(view):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    doc.core_properties.author = 'Studydy'
    doc.core_properties.last_modified_by = 'Studydy'
    doc.core_properties.comments = ''
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2'):
        style = doc.styles[name]
        style.font.name = 'Noto Sans CJK TC'
        fonts = style.element.get_or_add_rPr().find(qn('w:rFonts'))
        if fonts is None:
            fonts = OxmlElement('w:rFonts')
            style.element.get_or_add_rPr().append(fonts)
        fonts.set(qn('w:eastAsia'), 'Noto Sans CJK TC')
    doc.add_heading('重點整理' if view['kind'] == 'summary' else '完整筆記', 0)
    doc.add_paragraph(f'教材：{view["material_id"]}\n版本：{view["knowledge_structure_revision"]}')
    status = view['source_status']
    if status['quality'] == 'needs_review' or status['processing'] != 'succeeded' or status['decision'] != 'retain':
        doc.add_paragraph('教材仍有待核對或尚未完整處理的內容，請配合段落來源閱讀。')

    def paragraph(p):
        doc.add_heading(xml_text(p['heading']), 1)
        doc.add_paragraph(xml_text(p['text']))
        if p.get('formula'):
            doc.add_paragraph(xml_text(p['formula']))
        if p.get('table'):
            t = p['table']
            table = doc.add_table(rows=1, cols=len(t['headers']))
            table.style = 'Table Grid'
            for cell, value in zip(table.rows[0].cells, t['headers']):
                cell.text = xml_text(value)
            for row in t['rows']:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = xml_text(value)
        for e in p['evidence']:
            doc.add_paragraph(xml_text('來源：' + source_text(e)))

    for p in view['paragraphs']:
        paragraph(p)
        for a in view['annotations']:
            if a['paragraph_id'] == p['id']:
                doc.add_heading('個人註記', 2)
                doc.add_paragraph(xml_text(a['body']))
    orphaned = [a for a in view['annotations'] if a['paragraph_id'] is None]
    if orphaned:
        doc.add_heading('先前段落的註記', 1)
        for a in orphaned:
            doc.add_paragraph(xml_text('原版本：' + a['original_revision']))
            paragraph(a['original_paragraph'])
            doc.add_heading('個人註記', 2)
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
