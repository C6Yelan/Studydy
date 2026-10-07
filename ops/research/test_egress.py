import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('research_gate',Path(__file__).with_name('egress.py'))
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

@pytest.mark.parametrize('line,pins',[
    (b'CONNECT 127.0.0.1:443 HTTP/1.1',{'docs.python.org':'1.1.1.1'}),
    (b'CONNECT docs.python.org:80 HTTP/1.1',{'docs.python.org':'1.1.1.1'}),
    (b'CONNECT docs.python.org:443 HTTP/1.1',{'docs.python.org':'127.0.0.1'}),
    (b'CONNECT docs.python.org:443 HTTP/1.1',{'docs.python.org':'169.254.169.254'}),
    (b'CONNECT docs.python.org:443 HTTP/1.1',{'docs.python.org':'::1'}),
    (b'CONNECT user@docs.python.org:443 HTTP/1.1',{'docs.python.org':'1.1.1.1'}),
    (b'GET https://docs.python.org/ HTTP/1.1',{'docs.python.org':'1.1.1.1'}),
])
def test_rejects_unsafe_tunnel(line,pins):
    with pytest.raises(ValueError):gate.target(line,pins)

def test_uses_only_validated_pin_without_dns(monkeypatch):
    monkeypatch.setattr(gate.socket,'getaddrinfo',lambda *a:pytest.fail('must not resolve again'))
    assert gate.target(b'CONNECT docs.python.org:443 HTTP/1.1',{'docs.python.org':'1.1.1.1'})=='1.1.1.1'


def test_gate_rejects_unknown_connect_before_any_upstream(monkeypatch):
    import threading
    monkeypatch.setattr(gate.socket,'create_connection',lambda *a,**kw:pytest.fail('unsafe upstream must not open'))
    with gate.Gate({'docs.python.org':'1.1.1.1'}) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with gate.socket.socket() as client:
                client.settimeout(2);client.connect(server.server_address)
                client.sendall(b'CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n')
                assert client.recv(4096).startswith(b'HTTP/1.1 403 Forbidden')
        finally:
            server.shutdown();thread.join()
