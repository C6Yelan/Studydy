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
import pymupdf
import olefile
import markdown_it
from markdown_it import MarkdownIt
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P='http://schemas.openxmlformats.org/presentationml/2006/main'
MAX_FILE_BYTES=100*1024*1024
MIME={'.pdf':'application/pdf','.doc':'application/msword','.ppt':'application/vnd.ms-powerpoint','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','.txt':'text/plain','.md':'text/markdown'}

def wrap_text(line):
    parts=[]; current=''; width=0
    for c in line:
        advance=2 if unicodedata.east_asian_width(c) in ('W','F') else 1
        if width+advance>72: parts.append(current);current='';width=0
        current+=c;width+=advance
    return [*parts,current]

def validate(source):
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('MATERIAL_TOO_LARGE')
    ext = source.suffix.lower()
    if ext == '.pdf':
        try:
            with pymupdf.open(source) as doc:
                if not doc.is_pdf or doc.needs_pass or not doc.page_count:
                    raise ValueError('PDF_UNUSABLE')
        except pymupdf.FileDataError:
            raise ValueError('PDF_DAMAGED') from None
    elif ext in ['.doc', '.ppt']:
        try:
            if not olefile.isOleFile(source):raise ValueError('OFFICE_INVALID_PACKAGE')
            with olefile.OleFileIO(source,raise_defects=olefile.DEFECT_INCORRECT) as compound:
                parts={part.casefold() for path in compound.listdir(streams=True,storages=True) for part in path}
                if any(part in {'vba','macros','objectpool','activex','encryptedpackage','encryptioninfo','encryptedsummary'} or part.startswith('_vba_project') for part in parts):
                    raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                required='WordDocument' if ext=='.doc' else 'PowerPoint Document'
                if not compound.exists(required):raise ValueError('OFFICE_TYPE_MISMATCH')
        except OSError:
            raise ValueError('OFFICE_INVALID_PACKAGE') from None
    elif ext in ['.docx', '.pptx']:
        try:
            with zipfile.ZipFile(source) as z:
                infos=z.infolist()
                for i in infos:
                    if PurePosixPath(i.filename).is_absolute() or '..' in PurePosixPath(i.filename).parts or '\\' in i.filename:
                        raise ValueError('ZIP_PATH')
                    if i.flag_bits & 1 or 'vbaproject' in i.filename.lower() or '/embeddings/' in i.filename.lower() or '/activex/' in i.filename.lower():
                        raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                    if i.filename.endswith('.rels'):
                        root=ET.fromstring(z.read(i))
                        if any(e.get('TargetMode')=='External' for e in root):
                            raise ValueError('OFFICE_EXTERNAL_RELATIONSHIP')
                if '[Content_Types].xml' not in z.namelist():raise ValueError('OFFICE_INVALID_PACKAGE')
                content_types=z.read('[Content_Types].xml').lower()
                if any(marker in content_types for marker in (b'macroenabled',b'vbaproject',b'activex',b'oleobject')):
                    raise ValueError('OFFICE_ACTIVE_OR_ENCRYPTED')
                main='word/document.xml' if ext=='.docx' else 'ppt/presentation.xml'
                if main not in z.namelist():
                    raise ValueError('OFFICE_TYPE_MISMATCH')
        except zipfile.BadZipFile:
            raise ValueError('OFFICE_INVALID_PACKAGE') from None
    elif ext in ['.txt', '.md']:
        try: source.read_text(encoding='utf-8-sig')
        except UnicodeError: raise ValueError('UTF8_REQUIRED') from None
    else:
        raise ValueError('UNSUPPORTED_MEDIA_TYPE')

def text_html(source):
    text=source.read_text(encoding='utf-8-sig')
    anchors={}
    if source.suffix=='.txt':
        elements=[]
        for i,line in enumerate(text.splitlines(),1):
            key=f'line-{i}'; anchors[key]={'line_start':i,'line_end':i}
            elements.append(f'<p id="{key}">{"<br/>".join(html.escape(part) for part in wrap_text(line.expandtabs(4))) or "&#160;"}</p>')
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
    source=Path(sys.argv[1]);out=Path(sys.argv[2]);media=sys.argv[3]
    if source.suffix not in MIME or MIME[source.suffix]!=media:
        raise ValueError('UNSUPPORTED_MEDIA_TYPE')
    if pymupdf.VersionBind!='1.28.0' or markdown_it.__version__!='3.0.0' or olefile.__version__!='0.47':
        raise ValueError('NORMALIZER_VERSION_MISMATCH')
    validate(source)
    target=out/'normalized.pdf';ext=source.suffix
    if ext=='.pdf':target.write_bytes(source.read_bytes())
    elif ext in ('.docx','.pptx','.doc','.ppt'):
        version=subprocess.check_output(['/usr/lib/libreoffice/program/soffice','--version'],text=True)
        if '26.2.5.2' not in version:raise ValueError('NORMALIZER_VERSION_MISMATCH')
        options={key:{'type':'boolean','value':'false'} for key in ['ExportHiddenSlides','ExportNotes','ExportNotesPages','ExportOnlyNotesPages','ExportFormFields']}
        export='impress_pdf_Export' if ext in ('.pptx','.ppt') else 'writer_pdf_Export'
        # 獨立 profile 無可信任文件目錄，最高巨集安全層級不執行文件巨集。
        profile=Path('/tmp/profile/user');profile.mkdir(parents=True,exist_ok=True)
        (profile/'registrymodifications.xcu').write_text('<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>')
        completed=subprocess.run(['/usr/lib/libreoffice/program/soffice','-env:UserInstallation=file:///tmp/profile',
            '--headless','--nologo','--nodefault','--norestore','--convert-to','pdf:'+export+':'+json.dumps(options),
            '--outdir',str(out),str(source)],capture_output=True,timeout=50)
        generated=out/(source.stem+'.pdf')
        if completed.returncode or not generated.is_file():raise ValueError('NORMALIZATION_FAILED')
        generated.rename(target)
    else: records,_=story_pdf(source,target)
    validate(target)
    with pymupdf.open(target) as doc:
        if ext=='.pdf':records=[{'normalized_page':i+1,'origin_locator':{'original_page':i+1},'accuracy':'exact'} for i in range(len(doc))]
        elif ext=='.docx':records=docx_mapping(source,doc)
        elif ext=='.pptx':records,_=pptx_mapping(source,doc)
        elif ext in ('.doc','.ppt'):
            # 舊二進位格式不假造原始段落／投影片位置，保留 normalized PDF 頁與原檔下載。
            records=[]
        mapping={'schema':'source-mapping/v1','format':ext[1:],'original_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                 'normalized_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'page_count':len(doc),'records':records,
                 'quality_notice':'PDF 優先；自動轉換品質不保證。'}
    (out/'mapping.json').write_text(json.dumps(mapping,ensure_ascii=False,separators=(',',':')))

if __name__=='__main__':
    try:main()
    except Exception as error:
        allowed={'UNSUPPORTED_MEDIA_TYPE','MATERIAL_TOO_LARGE','PDF_UNUSABLE','PDF_DAMAGED','ZIP_PATH',
                 'OFFICE_ACTIVE_OR_ENCRYPTED','OFFICE_EXTERNAL_RELATIONSHIP','OFFICE_TYPE_MISMATCH','OFFICE_INVALID_PACKAGE',
                 'UTF8_REQUIRED','SLIDE_PAGE_COUNT_MISMATCH','UNSAFE_RENDER_HTML','NORMALIZER_VERSION_MISMATCH'}
        print(str(error) if str(error) in allowed else 'NORMALIZATION_FAILED')
        raise SystemExit(1)
