"""外連素材取得或失敗都可轉檔；原檔與一般超連結保留。"""
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import xml.etree.ElementTree as ET
import zipfile

import pymupdf
import pytest
from document_normalization.converter import MIME, conversion_policy, convert
from document_normalization.renderer import office_resources, validate

REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
OFFICE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
DRAWING = 'http://schemas.openxmlformats.org/drawingml/2006/main'
FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def resource_server():
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 32, 32), False)
    pix.clear_with(90)
    image = pix.tobytes('png')
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == '/image.png':
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(image)
            else:
                self.send_error(404)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', image, hits
    finally:
        server.shutdown(); thread.join(); server.server_close()


def office_source(extension, url):
    """現有合成教材加入外連圖及超連結。"""
    ppt = extension == '.pptx'
    part = 'ppt/slides/slide1.xml' if ppt else 'word/document.xml'
    rels = 'ppt/slides/_rels/slide1.xml.rels' if ppt else 'word/_rels/document.xml.rels'
    picture = f'''<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" xmlns:a="{DRAWING}" xmlns:r="{OFFICE}">
      <pic:nvPicPr><pic:cNvPr id="901" name="External picture"/><pic:cNvPicPr/></pic:nvPicPr>
      <pic:blipFill><a:blip r:link="rIdExternalImage"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
      <pic:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="914400" cy="914400"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
    </pic:pic>'''
    output = io.BytesIO()
    with zipfile.ZipFile(FIXTURES / ('sample' + extension)) as original, zipfile.ZipFile(output, 'w') as archive:
        root = ET.fromstring(original.read(part))
        if ppt:
            ns = 'http://schemas.openxmlformats.org/presentationml/2006/main'
            picture = picture.replace('http://schemas.openxmlformats.org/drawingml/2006/picture', ns).replace('</pic:nvPicPr>', '<pic:nvPr/></pic:nvPicPr>')
            root.find('.//{'+ns+'}spTree').append(ET.fromstring(picture))
        else:
            ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            root.find('.//{'+ns+'}body').insert(0, ET.fromstring(f'''<w:p xmlns:w="{ns}" xmlns:a="{DRAWING}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
              <w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/><wp:docPr id="901" name="External picture"/>
              <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">{picture}</a:graphicData></a:graphic>
              </wp:inline></w:drawing></w:r></w:p>'''))
        relations = ET.fromstring(original.read(rels)) if rels in original.namelist() else ET.Element(f'{{{REL}}}Relationships')
        for identity, kind, target in [('rIdExternalImage','image',url), ('rIdWebsite','hyperlink',url.rsplit('/',1)[0]+'/do-not-fetch-link')]:
            ET.SubElement(relations, f'{{{REL}}}Relationship', {'Id':identity,'Type':OFFICE+'/'+kind,'Target':target,'TargetMode':'External'})
        for entry in original.infolist():
            if entry.filename not in (part, rels): archive.writestr(entry, original.read(entry))
        archive.writestr(part, ET.tostring(root)); archive.writestr(rels, ET.tostring(relations))
    return output.getvalue()


@pytest.mark.parametrize('extension', ['.pptx','.docx'])
@pytest.mark.parametrize('available', [True,False])
def test_external_resource_is_embedded_or_skipped(tmp_path, resource_server, extension, available):
    base,image,hits = resource_server
    source = tmp_path / ('original'+extension)
    source.write_bytes(office_source(extension, base+('/image.png' if available else '/missing.png')))
    original = source.read_bytes()
    validate(source)
    prepared = office_resources(source, tmp_path/('render'+extension))
    assert source.read_bytes() == original
    with zipfile.ZipFile(prepared) as package:
        relations = [r for n in package.namelist() if n.endswith('.rels') for r in ET.fromstring(package.read(n))]
        assert any(r.get('Id')=='rIdWebsite' and r.get('TargetMode')=='External' for r in relations)
        assert any(r.get('Id')=='rIdExternalImage' for r in relations) == available
        assert any(package.read(n)==image for n in package.namelist()) == available
    assert hits == ['/image.png' if available else '/missing.png']


@pytest.mark.parametrize('extension', ['.pptx','.docx'])
@pytest.mark.parametrize('available', [True,False])
def test_real_conversion_keeps_text_and_external_image(resource_server, extension, available):
    base,image,hits = resource_server
    data = office_source(extension, base+('/image.png' if available else '/missing.png'))
    pdf,mapping = convert(data, extension, MIME[extension], conversion_policy())
    assert mapping['original_sha256'] == hashlib.sha256(data).hexdigest()
    with pymupdf.open(stream=pdf,filetype='pdf') as doc:
        assert len(doc)==mapping['page_count']
        assert ''.join(page.get_text() for page in doc).strip()
        if available:
            assert any(doc.extract_image(xref)['width']==32 for page in doc for xref,*_ in page.get_images())
    assert hits == ['/image.png' if available else '/missing.png']
