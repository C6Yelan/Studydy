"""外連素材取得或失敗都可轉檔；原檔與一般超連結保留。"""
import hashlib
import io
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import xml.etree.ElementTree as ET
import zipfile

import pymupdf
import pytest
from document_normalization.converter import MIME, conversion_policy, convert
from document_normalization.renderer import office_resources, validate
from document_normalization import converter, renderer

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
            if self.path.startswith('/image.png'):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                self.wfile.write(image)
            elif self.path in ('/redirect', '/private', '/loop'):
                self.send_response(302)
                self.send_header('Location', {'/redirect':'/image.png', '/private':f'http://127.0.0.1:{self.server.server_port}/image.png', '/loop':'/loop'}[self.path])
                self.end_headers()
            elif self.path == '/text':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'not an image')
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


@pytest.fixture
def public_resources(monkeypatch, resource_server):
    """驗證器看到固定公網 DNS；測試 socket 才將該 IP 接到隔離的素材伺服器。"""
    base, image, hits = resource_server
    port = int(base.rsplit(':', 1)[1])
    original_lookup = socket.getaddrinfo
    original_socket = socket.socket
    lookups = []
    def lookup(host, port, **kwargs):
        lookups.append(host)
        if host == 'assets.test':
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', port))]
        return original_lookup(host, port, **kwargs)
    class TestSocket(original_socket):
        def connect(self, address):
            if address == ('93.184.216.34', port):
                address = ('127.0.0.1', port)
            return super().connect(address)
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    monkeypatch.setattr(socket, 'socket', TestSocket)
    return f'http://assets.test:{port}', image, hits, lookups


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
def test_external_resource_is_embedded_or_skipped(tmp_path, public_resources, extension, available):
    base,image,hits,lookups = public_resources
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
    assert lookups == ['assets.test']


@pytest.mark.parametrize('extension', ['.pptx','.docx'])
@pytest.mark.parametrize('available', [True,False])
def test_real_conversion_skips_private_image_and_keeps_text(monkeypatch, resource_server, extension, available):
    base,image,hits = resource_server
    data = office_source(extension, base+('/image.png' if available else '/missing.png'))
    original = converter.subprocess.Popen
    commands = []
    def spawn(command, *args, **kwargs):
        if 'bwrap' in command:
            commands.append(command)
        return original(command, *args, **kwargs)
    monkeypatch.setattr(converter.subprocess, 'Popen', spawn)
    pdf,mapping = convert(data, extension, MIME[extension], conversion_policy())
    assert mapping['original_sha256'] == hashlib.sha256(data).hexdigest()
    with pymupdf.open(stream=pdf,filetype='pdf') as doc:
        assert len(doc)==mapping['page_count']
        assert ''.join(page.get_text() for page in doc).strip()
        assert not any(doc.extract_image(xref)['width']==32 for page in doc for xref,*_ in page.get_images())
    assert hits == []
    assert len(commands) == 2
    assert '--share-net' in commands[0] and '--prepare-office' in commands[0]
    assert '--share-net' not in commands[1] and '--prepare-office' not in commands[1]


@pytest.mark.parametrize('extension', ['.pptx','.docx'])
@pytest.mark.parametrize('available', [True,False])
def test_downloaded_public_image_survives_offline_conversion(tmp_path, public_resources, extension, available):
    base,image,hits,_ = public_resources
    source = tmp_path / ('original'+extension)
    source.write_bytes(office_source(extension, base+('/image.png' if available else '/missing.png')))
    prepared = office_resources(source, tmp_path / ('prepared'+extension)).read_bytes()
    pdf,mapping = convert(prepared, extension, MIME[extension], conversion_policy())
    assert mapping['original_sha256'] == hashlib.sha256(prepared).hexdigest()
    with pymupdf.open(stream=pdf,filetype='pdf') as doc:
        assert ''.join(page.get_text() for page in doc).strip()
        assert any(doc.extract_image(xref)['width']==32 for page in doc for xref,*_ in page.get_images()) == available
    assert hits == ['/image.png' if available else '/missing.png']


@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['10.0.0.1'], ['192.168.1.1'], ['169.254.169.254'], ['::1'], ['fc00::1'], ['::ffff:127.0.0.1'], ['93.184.216.34', '10.0.0.1']])
def test_external_image_rejects_nonpublic_dns_before_connect(monkeypatch, addresses):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', (address, 80)) for address in addresses])
    def no_connection(*args, **kwargs):
        pytest.fail('rejected address must not open a socket')
    monkeypatch.setattr(socket, 'socket', no_connection)
    with pytest.raises(ValueError):
        renderer._image_resource('http://assets.test/image.png', limit=100, deadline=time.monotonic()+15)


@pytest.mark.parametrize('path', ['/redirect', '/private', '/loop', '/text', '/image.png?version=2'])
def test_external_image_redirect_and_type_boundaries(public_resources, path):
    base,image,hits,lookups = public_resources
    if path in ('/redirect', '/image.png?version=2'):
        assert renderer._image_resource(base+path, limit=len(image), deadline=time.monotonic()+15) == (image, 'image/png')
    else:
        with pytest.raises(ValueError):
            renderer._image_resource(base+path, limit=len(image), deadline=time.monotonic()+15)
    assert hits == ({'/redirect':['/redirect', '/image.png'], '/loop':['/loop']*5}.get(path, [path]))
    assert lookups == (['assets.test', '127.0.0.1'] if path == '/private' else ['assets.test']*len(hits))


def test_external_image_retains_byte_and_time_limits(public_resources):
    base,image,hits,_ = public_resources
    with pytest.raises(ValueError):
        renderer._image_resource(base+'/image.png', limit=len(image)-1, deadline=time.monotonic()+15)
    with pytest.raises(TimeoutError):
        renderer._image_resource(base+'/image.png', limit=len(image), deadline=time.monotonic()-1)
    assert hits == ['/image.png']


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://assets.test/image.png', 'http://user:password@assets.test/image.png', 'http://assets.test/\nimage.png'])
def test_external_image_rejects_unsafe_url_before_dns(monkeypatch, url):
    def no_dns(*args, **kwargs):
        pytest.fail('rejected URL must not resolve')
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    with pytest.raises(ValueError):
        renderer._image_resource(url, limit=100, deadline=time.monotonic()+15)


def test_https_uses_verified_hostname_with_pinned_socket(monkeypatch, public_resources):
    base,image,hits,lookups = public_resources
    tls_hosts = []
    class TLSContext:
        def wrap_socket(self, raw, *, server_hostname):
            tls_hosts.append(server_hostname)
            return raw
    monkeypatch.setattr(renderer.ssl, 'create_default_context', TLSContext)
    assert renderer._image_resource(base.replace('http:', 'https:')+'/image.png', limit=len(image), deadline=time.monotonic()+15) == (image, 'image/png')
    assert tls_hosts == lookups == ['assets.test']
    assert hits == ['/image.png']
