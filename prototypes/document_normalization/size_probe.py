"""上限量測：只複製 renderer 做實驗，絕不修改／呼叫正式 API 或模型。"""
from __future__ import annotations
import argparse,hashlib,json,os,random,resource,signal,struct,subprocess,time,zlib,zipfile
from pathlib import Path
import xml.etree.ElementTree as E
import pymupdf

ROOT=Path(__file__).resolve().parents[2]
MIB=1024**2
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'
MIME={'.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','.txt':'text/plain','.md':'text/markdown'}

def png(seed,width=1024,height=1024):
    pixels=random.Random(seed).randbytes(width*height*3)
    data=b''.join(b'\0'+pixels[i:i+width*3] for i in range(0,len(pixels),width*3))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(data,1))+chunk(b'IEND',b'')

def image_docx(path,count,edge=1024):
    rels=E.Element(f'{{{PKG}}}Relationships');body=[]
    with zipfile.ZipFile(path,'w',zipfile.ZIP_STORED) as z:
        for i in range(count):
            z.writestr(f'word/media/img{i}.png',png(1000+i,edge,edge))
            E.SubElement(rels,f'{{{PKG}}}Relationship',Id=f'img{i}',Type=R+'/image',Target=f'media/img{i}.png')
            body.append(f'''<w:p>{'<w:pPr><w:pageBreakBefore/></w:pPr>' if i else ''}<w:r><w:t>PAGE_{i+1:04d}_IMAGE</w:t></w:r></w:p>
<w:p><w:r><w:drawing><wp:inline><wp:extent cx="5400000" cy="5400000"/><wp:docPr id="{i+1}" name="image{i}"/>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
<pic:nvPicPr><pic:cNvPr id="0" name="image{i}"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="img{i}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5400000" cy="5400000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>''')
        doc=f'''<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>{''.join(body)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="720" w:bottom="720" w:left="720" w:right="720"/></w:sectPr></w:body></w:document>'''
        z.writestr('word/document.xml',doc);z.writestr('word/_rels/document.xml.rels',E.tostring(rels))
        z.writestr('[Content_Types].xml',f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>')

def image_pptx(path,count,edge=1024):
    with zipfile.ZipFile(ROOT/'backend/tests/fixtures/sample.pptx') as original,zipfile.ZipFile(path,'w',zipfile.ZIP_STORED) as z:
        presentation=E.fromstring(original.read('ppt/presentation.xml'));slides=presentation.find(f'{{{P}}}sldIdLst');slides.clear()
        rels=E.fromstring(original.read('ppt/_rels/presentation.xml.rels'))
        for rel in list(rels):
            if rel.get('Type')==R+'/slide':rels.remove(rel)
        content_types=E.fromstring(original.read('[Content_Types].xml'))
        for item in list(content_types):
            if item.get('PartName','').startswith('/ppt/slides/'):content_types.remove(item)
        if not any(e.get('Extension')=='png' for e in content_types):E.SubElement(content_types,f'{{{CT}}}Default',Extension='png',ContentType='image/png')
        for name in original.namelist():
            if name.startswith('ppt/slides/') or name in ('ppt/presentation.xml','ppt/_rels/presentation.xml.rels','[Content_Types].xml'):continue
            z.writestr(name,original.read(name))
        for i in range(count):
            E.SubElement(slides,f'{{{P}}}sldId',{'id':str(256+i),f'{{{R}}}id':f'benchSlide{i}'})
            E.SubElement(rels,f'{{{PKG}}}Relationship',Id=f'benchSlide{i}',Type=R+'/slide',Target=f'slides/slide{i+1}.xml')
            E.SubElement(content_types,f'{{{CT}}}Override',PartName=f'/ppt/slides/slide{i+1}.xml',ContentType='application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
            slide=E.fromstring(original.read('ppt/slides/slide1.xml'));slide.attrib.pop('show',None)
            for index,text in enumerate(slide.iter(f'{{{A}}}t')):text.text=f'PAGE_{i+1:04d}_IMAGE' if index==0 else ''
            picture=E.fromstring(f'''<p:pic xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:nvPicPr><p:cNvPr id="100" name="bench-image"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="benchImage"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="400000" y="1600000"/><a:ext cx="7000000" cy="3500000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>''')
            slide.find(f'{{{P}}}cSld/{{{P}}}spTree').append(picture)
            sr=E.Element(f'{{{PKG}}}Relationships')
            E.SubElement(sr,f'{{{PKG}}}Relationship',Id='layout',Type=R+'/slideLayout',Target='../slideLayouts/slideLayout2.xml')
            E.SubElement(sr,f'{{{PKG}}}Relationship',Id='benchImage',Type=R+'/image',Target=f'../media/bench{i}.png')
            z.writestr(f'ppt/slides/slide{i+1}.xml',E.tostring(slide));z.writestr(f'ppt/slides/_rels/slide{i+1}.xml.rels',E.tostring(sr))
            z.writestr(f'ppt/media/bench{i}.png',png(1000+i,edge,edge))
        z.writestr('ppt/presentation.xml',E.tostring(presentation));z.writestr('ppt/_rels/presentation.xml.rels',E.tostring(rels));z.writestr('[Content_Types].xml',E.tostring(content_types))

def dense_docx(path,paragraphs):
    with zipfile.ZipFile(ROOT/'backend/tests/fixtures/sample.docx') as original,zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name in original.namelist():
            data=original.read(name)
            if name=='word/document.xml':
                root=E.fromstring(data);body=root.find(f'{{{W}}}body');section=body.find(f'{{{W}}}sectPr');body.clear()
                for i in range(paragraphs):
                    p=E.SubElement(body,f'{{{W}}}p');r=E.SubElement(p,f'{{{W}}}r')
                    E.SubElement(r,f'{{{W}}}t').text=f'Paragraph {i:05d}: '+random.Random(i).randbytes(36).hex()
                body.append(section);data=E.tostring(root)
            z.writestr(name,data)

def make_text(path,kib):
    lines=[];size=0;i=0
    while size<kib*1024:
        row=f'LINE_{i:06d} 教材文字 '+random.Random(i).randbytes(24).hex()+'\n'
        if path.suffix=='.md' and i%12==0:row='## '+row
        lines.append(row);size+=len(row.encode());i+=1
    path.write_text(''.join(lines));return i

def process_tree_rss(pid):
    try:
        raw=subprocess.check_output(['ps','-eo','pid=,ppid=,rss='],text=True)
        rows=[tuple(map(int,line.split())) for line in raw.splitlines()]
        pids={pid}
        while True:
            added={p for p,parent,_ in rows if parent in pids}-pids
            if not added:break
            pids.update(added)
        return sum(rss for p,_,rss in rows if p in pids)/1024
    except (OSError,ValueError,subprocess.SubprocessError):return 0

def limits():
    for kind,value in [(resource.RLIMIT_CPU,45),(resource.RLIMIT_AS,2*1024**3),(resource.RLIMIT_FSIZE,100*MIB),(resource.RLIMIT_NOFILE,128),(resource.RLIMIT_NPROC,1024)]:resource.setrlimit(kind,(value,value))

def run_case(source,renderer,out,policy,expected_pages=None,lines=None):
    out.mkdir();python=ROOT/'backend/.venv/bin/python';base=python.resolve().parent.parent;site=python.parent.parent/'lib/python3.12/site-packages'
    command=['bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
        '--ro-bind','/etc/fonts','/etc/fonts','--ro-bind','/etc/libreoffice','/etc/libreoffice','--ro-bind','/etc/ld.so.cache','/etc/ld.so.cache','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
        '--ro-bind',str(base),'/runtime','--ro-bind',str(site),'/runtime/lib/python3.12/site-packages','--ro-bind',str(renderer),'/renderer.py','--ro-bind',str(source.parent),'/input','--bind',str(out),'/output','--clearenv',
        '--setenv','HOME','/tmp','--setenv','PATH','/usr/bin','--setenv','LANG','C.UTF-8','--setenv','SAL_USE_VCLPLUGIN','svp','--setenv','XDG_CACHE_HOME','/tmp/cache',
        '/runtime/bin/python3.12','/renderer.py','/input/'+source.name,'/output',MIME[source.suffix]]
    t=time.monotonic();peak=0
    with (out/'stdout.txt').open('wb') as stdout,(out/'stderr.txt').open('wb') as stderr:
        proc=subprocess.Popen(['/usr/bin/time','-f','wall=%e\nuser=%U\nsystem=%S\nmax_process_rss_kib=%M','-o',str(out/'time.txt'),*command],stdout=stdout,stderr=stderr,start_new_session=True,preexec_fn=limits)
        timed_out=False
        while proc.poll() is None:
            peak=max(peak,process_tree_rss(proc.pid))
            if time.monotonic()-t>60:
                os.killpg(proc.pid,signal.SIGKILL);timed_out=True;break
            time.sleep(.1)
        proc.wait()
    result={'name':source.stem,'format':source.suffix[1:],'policy':policy,'input_mib':round(source.stat().st_size/MIB,3),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'wall_seconds':round(time.monotonic()-t,3),'peak_tree_rss_mib':round(peak,1),'exit_code':proc.returncode,'timed_out':timed_out}
    if source.suffix in ('.docx','.pptx'):
        with zipfile.ZipFile(source) as z:result.update(unpacked_mib=round(sum(i.file_size for i in z.infolist())/MIB,3),zip_entries=len(z.infolist()))
    if lines:result['input_lines']=lines
    target=out/'normalized.pdf'
    result['reason']='WALL_TIMEOUT' if timed_out else (out/'stdout.txt').read_text().strip()[-200:]
    if proc.returncode==0 and target.exists():
        with pymupdf.open(target) as document:
            result.update(output_mib=round(target.stat().st_size/MIB,3),pages=len(document))
            if expected_pages is not None:
                markers=[f'PAGE_{i+1:04d}_IMAGE' in document[i].get_text() for i in range(min(len(document),expected_pages))]
                result['expected_page_content_present']=len(document)==expected_pages and all(markers)
                result['image_on_every_page']=all(bool(page.get_images()) for page in document)
            if source.suffix in ('.txt','.md'):
                result['last_line_present']=f'LINE_{lines-1:06d}' in document[-1].get_text()
            if not (out.parent/f'{source.suffix[1:]}-preview.png').exists():document[0].get_pixmap(matrix=pymupdf.Matrix(.6,.6)).save(out.parent/f'{source.suffix[1:]}-preview.png')
        result['status']='PASS' if result.get('expected_page_content_present',True) and result.get('image_on_every_page',True) and result.get('last_line_present',True) else 'CONTENT_CHECK_FAILED'
    else:result['status']='REJECTED_OR_FAILED'
    for name in ('normalized.pdf','mapping.json'):(out/name).unlink(missing_ok=True)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False);sources=root/'sources';sources.mkdir()
    production=(ROOT/'backend/src/document_normalization/renderer.py').read_text()
    variants={}
    for label in ['current','size_only']:
        code=production
        if label!='current':
            code=code.replace('source.stat().st_size > 20*1024**2','source.stat().st_size > 200*1024**2')
            code=code.replace("                if len(infos)>1024 or sum(i.file_size for i in infos)>20*1024**2:\n                    raise ValueError('ZIP_LIMIT')\n",'')
            code=code.replace("                    if i.file_size > max(i.compress_size,1)*100:\n                        raise ValueError('ZIP_RATIO')\n",'')
            code=code.replace("            if page>30: raise ValueError('PAGE_LIMIT')\n",'')
            code=code.replace("        if len(doc)>500:raise ValueError('PAGE_LIMIT')\n",'')
        path=root/f'renderer-{label}.py';path.write_text(code);variants[label]=path
    report={'production_renderer_sha256':hashlib.sha256(production.encode()).hexdigest(),'model_calls':0,'production_mutations':0,
            'memory_measurement':'sampled sum of descendant RSS at ~100ms; shared pages counted more than once, not PSS',
            'candidate_changes':'private copy only: file/output allowance 200MiB; remove page count, ZIP expanded size/ratio/count quotas per user direction. Benchmark watchdog CPU45s/AS2GiB/file100MiB/wall60s remains for measuring bounded failures; not proposed product quotas', 'cases':[]}
    def run(path,variant,expected=None,lines=None):
        index=len(report['cases']);result=run_case(path,variants[variant],root/f'case-{index:02d}',variant,expected,lines);report['cases'].append(result)
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    for ext,generator in [('.pptx',image_pptx),('.docx',image_docx)]:
        for count in [6,8,16,24,33,50]:
            path=sources/f'images-{count}{ext}';generator(path,count)
            if count in (6,8):run(path,'current',count)
            run(path,'size_only',count)
            if count in (16,33):run(path,'size_only',count)
            path.unlink()
        path=sources/f'many-pages{ext}';generator(path,100,256);run(path,'size_only',100);path.unlink()
    for count in [1000,2500]:
        path=sources/f'dense-{count}.docx';dense_docx(path,count);run(path,'size_only');path.unlink()
    for ext in ['.txt','.md']:
        for kib in [24,96,256,1024]:
            path=sources/f'text-{kib}KiB{ext}';lines=make_text(path,kib)
            if kib in (24,96):run(path,'current',lines=lines)
            run(path,'size_only',lines=lines)
            path.unlink()
    print('REPORT',root/'report.json',flush=True)
if __name__=='__main__':main()
