"""B2-P 合成文件實驗；不匯入產品 API、DB、worker 或 AI。"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

import pymupdf
# 使用已安裝的系統 parser，僅供獨立 prototype；不修改共用 venv。
sys.path.append('/usr/lib/python3/dist-packages')
from markdown_it import MarkdownIt
import markdown_it

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/package/2006/relationships'
OPTIONS = {k: {'type': 'boolean', 'value': 'false'} for k in
           ['ExportHiddenSlides', 'ExportNotes', 'ExportNotesPages', 'ExportOnlyNotesPages', 'ExportFormFields']}
LIMITS = {'cpu_seconds': 30, 'address_space_bytes': 2*1024**3, 'file_bytes': 64*1024**2,
          'open_files': 128, 'processes_per_uid': 1024, 'wall_seconds': 60}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def limits():
    for kind, value in [(resource.RLIMIT_CPU, 30), (resource.RLIMIT_AS, 2*1024**3),
                        (resource.RLIMIT_FSIZE, 64*1024**2), (resource.RLIMIT_NOFILE, 128),
                        (resource.RLIMIT_NPROC, 1024)]:
        resource.setrlimit(kind, (value, value))


def bounded(command, timeout=60):
    start = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, preexec_fn=limits)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise ValueError('CONVERTER_TIMEOUT') from None
    if process.returncode:
        raise ValueError(f'CONVERTER_EXIT_{process.returncode}: {stderr.decode(errors="replace")[:250]}')
    return time.monotonic()-start


def sandbox(source_dir, output_dir):
    return ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', '/etc/fonts', '/etc/fonts', '--ro-bind', '/etc/libreoffice', '/etc/libreoffice', '--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home',
            '--ro-bind', str(source_dir), '/input', '--bind', str(output_dir), '/output',
            '--clearenv', '--setenv', 'HOME', '/tmp', '--setenv', 'PATH', '/usr/bin',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'SAL_USE_VCLPLUGIN', 'svp',
            '--setenv', 'XDG_CACHE_HOME', '/tmp/cache', '--setenv', 'XDG_CONFIG_HOME', '/tmp/config']


def office(source, output_dir, kind):
    output_dir.mkdir()
    profile = '-env:UserInstallation=file:///tmp/lo-profile'
    if kind == 'pdf':
        filter_name = 'impress_pdf_Export' if source.suffix in ['.pptx', '.fodp'] else 'writer_pdf_Export'
        export = 'pdf:'+filter_name+':'+json.dumps(OPTIONS, separators=(',', ':'))
    else:
        export = 'pptx:Impress MS PowerPoint 2007 XML'
    elapsed = bounded(sandbox(source.parent, output_dir) + [
        '/usr/lib/libreoffice/program/soffice', profile, '--headless', '--nologo', '--nodefault', '--norestore',
        '--convert-to', export, '--outdir', '/output', '/input/'+source.name])
    result = output_dir/(source.stem+'.'+kind)
    if not result.is_file():
        raise ValueError('CONVERTER_NO_OUTPUT')
    return result, elapsed


def validate(source):
    if source.stat().st_size > 20*1024**2:
        raise ValueError('INPUT_TOO_LARGE')
    ext = source.suffix.lower()
    if ext == '.pdf':
        try:
            with pymupdf.open(source) as doc:
                if not doc.is_pdf or doc.needs_pass or not doc.page_count:
                    raise ValueError('PDF_UNUSABLE')
        except pymupdf.FileDataError:
            raise ValueError('PDF_DAMAGED') from None
    elif ext in ['.docx', '.pptx']:
        try:
            with zipfile.ZipFile(source) as z:
                infos=z.infolist()
                if len(infos)>1024 or sum(i.file_size for i in infos)>20*1024**2:
                    raise ValueError('ZIP_LIMIT')
                for i in infos:
                    if PurePosixPath(i.filename).is_absolute() or '..' in PurePosixPath(i.filename).parts or '\\' in i.filename:
                        raise ValueError('ZIP_PATH')
                    if i.file_size > max(i.compress_size,1)*100:
                        raise ValueError('ZIP_RATIO')
                    if i.flag_bits & 1 or 'vbaproject' in i.filename.lower():
                        raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                    if i.filename.endswith('.rels'):
                        root=ET.fromstring(z.read(i))
                        if any(e.get('TargetMode')=='External' for e in root):
                            raise ValueError('OFFICE_EXTERNAL_RELATIONSHIP')
                main='word/document.xml' if ext=='.docx' else 'ppt/presentation.xml'
                if main not in z.namelist():
                    raise ValueError('OFFICE_TYPE_MISMATCH')
        except zipfile.BadZipFile:
            raise ValueError('OFFICE_INVALID_PACKAGE') from None
    elif ext in ['.txt', '.md']:
        try: source.read_text(encoding='utf-8-sig')
        except UnicodeError: raise ValueError('UTF8_REQUIRED') from None
    else:
        raise ValueError('UNSUPPORTED_FORMAT')


def docx_fixture(path):
    def paragraph(text, before=False):
        return '<w:p>'+('<w:pPr><w:pageBreakBefore/></w:pPr>' if before else '')+'<w:r><w:rPr><w:rFonts w:ascii="Liberation Sans" w:eastAsia="Noto Sans CJK TC"/></w:rPr><w:t xml:space="preserve">'+html.escape(text)+'</w:t></w:r></w:p>'
    body=paragraph('文件轉換測試 DOCX_TITLE')+paragraph('int a[3] = {1, 2, 3}; // CODE_LITERAL')
    body+=paragraph('REPEATED_PARAGRAPH')+paragraph('REPEATED_PARAGRAPH')
    body+='<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single"/><w:bottom w:val="single"/><w:insideH w:val="single"/></w:tblBorders></w:tblPr>'
    for i in range(65):
        body+='<w:tr><w:tc>'+paragraph(f'TABLE_ROW_{i+1:02} 表格內容')+'</w:tc><w:tc>'+paragraph(str(i*3))+'</w:tc></w:tr>'
    body+='</w:tbl>'+paragraph('FINAL_ANCHOR 最後一段',True)
    body+='<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:bottom="1000" w:left="1000" w:right="1000"/></w:sectPr>'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{R}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')


def fodp_fixture(path):
    slides=[]
    for i in range(1,4):
        text=f'SLIDE_{i}_MARKER 投影片 {i}' if i!=2 else 'HIDDEN_SLIDE_MARKER'
        slides.append(f'''<draw:page draw:name="Slide{i}" draw:style-name="{'hidden' if i==2 else 'visible'}" draw:master-page-name="Default">
<draw:frame svg:x="1cm" svg:y="1cm" svg:width="24cm" svg:height="10cm"><draw:text-box><text:p text:style-name="title">{text}</text:p><text:p>int x = 42; SLIDE_CODE_{i}</text:p><text:p>表格與公式為盡力轉換：a² + b² = c²</text:p></draw:text-box></draw:frame>
<presentation:notes><draw:frame presentation:class="notes" svg:x="1cm" svg:y="1cm" svg:width="20cm" svg:height="5cm"><draw:text-box><text:p>PRIVATE_NOTES_MARKER_{i}</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>''')
    path.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" office:mimetype="application/vnd.oasis.opendocument.presentation" office:version="1.3">
<office:automatic-styles><style:page-layout style:name="page"><style:page-layout-properties fo:page-width="28cm" fo:page-height="16cm" style:print-orientation="landscape"/></style:page-layout><style:style style:name="visible" style:family="drawing-page"><style:drawing-page-properties presentation:visibility="visible"/></style:style><style:style style:name="hidden" style:family="drawing-page"><style:drawing-page-properties presentation:visibility="hidden"/></style:style><style:style style:name="title" style:family="paragraph"><style:text-properties fo:font-size="24pt" style:font-name-asian="Noto Sans CJK TC"/></style:style></office:automatic-styles>
<office:master-styles><style:master-page style:name="Default" style:page-layout-name="page"/></office:master-styles><office:body><office:presentation>'''+''.join(slides)+'</office:presentation></office:body></office:document>')


def text_html(source):
    text=source.read_text(encoding='utf-8-sig')
    anchors={}
    if source.suffix=='.txt':
        elements=[]
        for i,line in enumerate(text.splitlines(),1):
            key=f'line-{i}'; anchors[key]={'line_start':i,'line_end':i}
            elements.append(f'<p id="{key}">{html.escape(line.expandtabs(4)) or "&#160;"}</p>')
        return '<div class="plain">'+''.join(elements)+'</div>', anchors
    parser=MarkdownIt('commonmark',{'html':False, 'linkify':False}).enable('table')
    tokens=parser.parse(text)
    for i,t in enumerate(tokens):
        if t.map and t.type not in ['inline']:
            key=f'block-{i}';anchors[key]={'line_start':t.map[0]+1,'line_end':t.map[1]}
            t.attrSet('id',key)
        if t.children:
            for child in t.children:
                if child.type=='image':
                    child.type='text';child.tag='';child.content='[圖片未載入]';child.children=None;child.attrs={}
                elif child.type in ['link_open','link_close']:
                    child.tag='span';child.attrs={}
    # fenced code renderer 原生不保留 token attrs，明確加入 source block id。
    fence=parser.renderer.rules['fence']
    def fenced(ts,idx,options,env):
        return fence(ts,idx,options,env).replace('<pre>',f'<pre id="{ts[idx].attrGet("id")}">',1)
    parser.renderer.rules['fence']=fenced
    return parser.renderer.render(tokens,parser.options,{}),anchors


def story_pdf(source,target):
    markup,anchors=text_html(source)
    if '<img' in markup or '<script' in markup or 'href=' in markup or 'src=' in markup:
        raise ValueError('UNSAFE_RENDER_HTML')
    story=pymupdf.Story(markup,user_css='body {font-family:sans-serif;font-size:11pt;} p {margin:0 0 6pt;} .plain p {white-space:pre-wrap;font-family:monospace;} pre {white-space:pre-wrap;} td,th {border:1px solid #aaa;padding:4pt;}')
    records=[]
    writer=pymupdf.DocumentWriter(str(target))
    try:
        page=0
        while True:
            page+=1
            if page>30: raise ValueError('PAGE_LIMIT')
            device=writer.begin_page(pymupdf.Rect(0,0,595,842))
            more,_=story.place(pymupdf.Rect(40,40,555,802))
            def position(pos):
                if pos.id in anchors and pos.open_close & 1:
                    records.append({'normalized_page':page,'region':list(pos.rect), 'origin_locator':anchors[pos.id], 'accuracy':'exact', 'anchor':pos.id})
            story.element_positions(position)
            story.draw(device);writer.end_page()
            if not more: break
    finally: writer.close()
    return records,markup


def docx_mapping(source,doc):
    with zipfile.ZipFile(source) as z: tree=ET.fromstring(z.read('word/document.xml'))
    texts=[''.join(p.itertext()) for p in tree.iter(f'{{{W}}}p')]
    counts=Counter(texts); records=[]
    for i,text in enumerate(texts,1):
        hits=[{'normalized_page':n+1,'region':list(rect)} for n,p in enumerate(doc) for rect in p.search_for(text)] if text else []
        status='exact' if counts[text]==1 and len(hits)==1 else 'ambiguous' if hits else 'unavailable'
        records.append({'origin_locator':{'document_part':'word/document.xml','paragraph':i},'accuracy':status,
                        'reason':'unique_text_match' if status=='exact' else 'repeated_or_split_text' if hits else 'no_exact_text_match', 'candidates':hits})
    return records


def pptx_mapping(source,doc):
    with zipfile.ZipFile(source) as z:
        tree=ET.fromstring(z.read('ppt/presentation.xml'))
        rels={x.get('Id'):x.get('Target') for x in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))}
        slides=[]
        for number,e in enumerate(tree.find(f'{{{P}}}sldIdLst'),1):
            target=rels[e.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            root=ET.fromstring(z.read('ppt/'+target))
            slides.append({'original_slide_number':number,'slide_id':e.get('id'),'hidden':root.get('show')=='0'})
    visible=[s for s in slides if not s['hidden']]
    if len(visible)!=len(doc):raise ValueError('SLIDE_PAGE_COUNT_MISMATCH')
    return [{'normalized_page':i+1,'origin_locator':s,'accuracy':'exact'} for i,s in enumerate(visible)],slides


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    sources=out/'sources';sources.mkdir(); rendered=out/'rendered';rendered.mkdir()
    docx_fixture(sources/'sample.docx');fodp_fixture(sources/'slides.fodp')
    deck,_=office(sources/'slides.fodp',out/'fixture-build','pptx')
    (sources/'sample.pptx').write_bytes(deck.read_bytes())
    (sources/'sample.txt').write_bytes(('文字來源 TXT_TITLE\r\n\tint a[3] = {1, 2, 3};\r\n\r\n'+'long_line_'*40+'\r\n'+'\n'.join(f'LINE_{i:03} 中文文字' for i in range(1,90))).encode())
    (sources/'sample.md').write_text('# Markdown 教材 MD_TITLE\n\n- 清單 LIST_MARKER\n- 第二項\n\n| 欄位 | 值 |\n|---|---|\n| 繁體中文 | 42 |\n\n```c\nint a[3] = {1, 2, 3};\n```\n\n[網站](https://example.invalid/)\n\n![外部圖片](https://example.invalid/tracker.png)\n\n<script>RAW_HTML_MARKER</script>\n\n![本機檔案](file:///etc/passwd)\n')
    with pymupdf.open() as d:
        d.new_page().insert_text((72,72),'PDF_ORIGINAL_MARKER');d[0].set_rotation(90);d.save(sources/'sample.pdf')
    version=subprocess.check_output(['libreoffice','--version'],text=True).strip()
    fonts=subprocess.check_output(['fc-list',':lang=zh','file','family'],text=True).splitlines()
    results={'versions':{'libreoffice':version,'pymupdf':pymupdf.VersionBind,'markdown_it':markdown_it.__version__},
             'fonts':fonts,'policy':{'office_options':OPTIONS,'limits':LIMITS,'markdown_html':False,'external_resources':'not loaded'},
             'formats':{},'negative_cases':{},'model_calls':0,'ocr_calls':0}
    for ext in ['pdf','docx','pptx','txt','md']:
        source=sources/f'sample.{ext}';validate(source);start=time.monotonic()
        if ext=='docx' and not Path('/usr/lib/libreoffice/share/registry/writer.xcd').is_file():
            results['formats'][ext]={'status':'BLOCKED', 'reason':'WRITER_COMPONENT_UNAVAILABLE', 'original_sha256':sha(source)}
            continue
        if ext in ['docx','pptx']: target,_=office(source,rendered/ext,'pdf')
        else:
            target=rendered/f'{ext}.pdf'
            if ext=='pdf':target.write_bytes(source.read_bytes())
            else:records,markup=story_pdf(source,target);(rendered/f'{ext}.html').write_text(markup)
        validate(target)
        with pymupdf.open(target) as doc:
            pages=[p.get_text() for p in doc]
            if ext=='docx': records=docx_mapping(source,doc)
            elif ext=='pptx': records,slides=pptx_mapping(source,doc)
            elif ext=='pdf': records=[{'normalized_page':i+1,'origin_locator':{'original_page':i+1},'accuracy':'exact'} for i in range(len(doc))]
            for i in range(min(2,len(doc))):doc[i].get_pixmap(matrix=pymupdf.Matrix(1,1)).save(rendered/f'{ext}-page-{i+1}.png')
            manifest={'source_name':source.name,'original_sha256':sha(source),'normalized_sha256':sha(target),
                      'normalized_file':str(target.relative_to(out)),'normalized_fonts':sorted({font[3] for page in doc for font in page.get_fonts()}),
                      'pages':len(doc),'page_map':[{'canonical_page':i+1,'normalized_page':i+1} for i in range(len(doc))],
                      'origin_mapping':records,'accuracy_counts':dict(Counter(x['accuracy'] for x in records)),
                      'elapsed_seconds':round(time.monotonic()-start,3),'extracted_text':pages}
            if ext=='pptx':manifest['slides']=slides
            (out/f'{ext}-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            results['formats'][ext]={k:v for k,v in manifest.items() if k not in ['origin_mapping','extracted_text','page_map']}
    # 拒絕例只使用自行產生的檔案，不接產品檔案。
    bad=sources/'damaged.pdf';bad.write_bytes(b'%PDF-broken')
    encrypted=sources/'encrypted.pdf'
    with pymupdf.open(sources/'sample.pdf') as d:d.save(encrypted,encryption=pymupdf.PDF_ENCRYPT_AES_256,user_pw='fixture-only',owner_pw='fixture-owner')
    invalid=sources/'invalid.txt';invalid.write_bytes(b'\xff\xfe\x80')
    invalid_md=sources/'invalid.md';invalid_md.write_bytes(b'\xff\x80')
    mismatch=sources/'mismatch.pptx';mismatch.write_bytes((sources/'sample.docx').read_bytes())
    traversal=sources/'traversal.docx'
    with zipfile.ZipFile(traversal,'w') as z:z.writestr('../escape','synthetic')
    bomb=sources/'ratio.docx'
    with zipfile.ZipFile(bomb,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('padding','x'*100000)
    remote=sources/'remote.docx'
    with zipfile.ZipFile(sources/'sample.docx') as old,zipfile.ZipFile(remote,'w') as z:
        for name in old.namelist():z.writestr(name,old.read(name))
        z.writestr('word/_rels/document.xml.rels',f'<Relationships xmlns="{R}"><Relationship Id="rX" TargetMode="External" Target="https://example.invalid/image.png"/></Relationships>')
    for source in [bad,encrypted,invalid,invalid_md,mismatch,traversal,bomb,remote]:
        try:validate(source);reason='UNEXPECTED_ACCEPT'
        except ValueError as e:reason=str(e)
        results['negative_cases'][source.name]=reason
    try:bounded(sandbox(sources,rendered)+['/usr/bin/sleep','5'],timeout=.05)
    except ValueError as e:results['negative_cases']['process_timeout']=str(e)
    # 明確證明隔離子程序不能連網，也看不到主機 home。
    check='import socket, pathlib; assert not pathlib.Path("/home/jerry").exists(); s=socket.socket(); s.settimeout(.2)\ntry: s.connect(("1.1.1.1",443))\nexcept OSError: pass\nelse: raise AssertionError("NETWORK_ALLOWED")'
    bounded(sandbox(sources,rendered)+['/usr/bin/python3','-c',check])
    results['isolation']='network namespace and host-home denial verified; LibreOffice resource limits enforced; Story runs in probe process with sanitized resource-free HTML'
    manifests={ext:json.loads((out/f'{ext}-manifest.json').read_text()) for ext in ['pdf','pptx','txt','md']}
    slide_text=''.join(manifests['pptx']['extracted_text'])
    checks={
        'pdf_bytes_unchanged':manifests['pdf']['original_sha256']==manifests['pdf']['normalized_sha256'],
        'slides_keep_original_numbers':[x['origin_locator']['original_slide_number'] for x in manifests['pptx']['origin_mapping']]==[1,3],
        'hidden_slide_excluded':'HIDDEN_SLIDE_MARKER' not in slide_text,
        'notes_excluded':'PRIVATE_NOTES_MARKER' not in slide_text,
        'visible_slide_content_retained':all(f'SLIDE_{i}_MARKER' in manifests['pptx']['extracted_text'][n] for n,i in enumerate([1,3])),
        'text_all_original_lines_mapped':{x['origin_locator']['line_start'] for x in manifests['txt']['origin_mapping']}==set(range(1,94)),
        'markdown_raw_html_is_text':'<script>RAW_HTML_MARKER</script>' in ''.join(manifests['md']['extracted_text']),
        'markdown_remote_image_not_rendered':'[圖片未載入]' in ''.join(manifests['md']['extracted_text']),
    }
    # mapping 的正／反例獨立於 DOCX renderer；不能當作 DOCX 轉檔成功。
    with pymupdf.open() as mapping_doc:
        page=mapping_doc.new_page()
        page.insert_text((50,50),'DOCX_TITLE')
        page.insert_text((50,100),'REPEATED_PARAGRAPH')
        page.insert_text((50,150),'REPEATED_PARAGRAPH')
        page.insert_text((50,200),'0')
        mapping=docx_mapping(sources/'sample.docx',mapping_doc)
        checks['mapping_repeated_paragraph_is_ambiguous']=all(x['accuracy']=='ambiguous' for x in mapping[2:4])
        checks['mapping_absent_anchor_is_unavailable']=mapping[0]['accuracy']=='unavailable'
        checks['mapping_unique_cell_text_is_exact']=mapping[5]['accuracy']=='exact'
    results['checks']=checks
    results['observed_limits']={
        'txt_long_unbroken_line_fully_extracted':('long_line_'*40) in ''.join(manifests['txt']['extracted_text']),
        'docx_conversion_executed':'pages' in results['formats']['docx'],
        'story_sandboxed':False,
        'nproc_limit_scope':'per UID, not per conversion; per-job cgroup quota not verified',
        'conversion_quality_guaranteed':False,
    }
    assert all(checks.values()), checks
    results['policy_sha256']=hashlib.sha256(json.dumps(results['policy'],sort_keys=True).encode()).hexdigest()
    (out/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    assert all(v!='UNEXPECTED_ACCEPT' for v in results['negative_cases'].values())
    print(json.dumps({k:results[k] for k in ['versions','formats','negative_cases','checks','observed_limits','isolation','model_calls','ocr_calls']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
