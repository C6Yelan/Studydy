"""以隔離 echo upstream 驗證真正 Nginx；無產品資料或模型請求。"""
import http.client
import json
import os
import subprocess
import time
from uuid import uuid4


def run(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    name = 'studydy-nginx-check-' + uuid4().hex[:10]
    frontend = os.environ['STUDYDY_TEST_FRONTEND_IMAGE']
    backend = os.environ['STUDYDY_TEST_BACKEND_IMAGE']
    echo = '''from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data=json.dumps(dict(self.headers)).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')));self.do_GET()
 def log_message(self,*args):pass
HTTPServer(('0.0.0.0',8001),Handler).serve_forever()
'''
    run('network', 'create', name)
    try:
        run('run', '-d', '--name', name+'-backend', '--network', name, '--network-alias', 'backend',
            '--entrypoint', '/app/backend/.venv/bin/python', backend, '-B', '-c', echo)
        run('run', '-d', '--name', name+'-frontend', '--network', name, '--read-only', '--tmpfs', '/tmp',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '-e', 'STUDYDY_UPLOAD_MAX_BYTES=1024',
            '-p', '127.0.0.1:4183:8080', frontend)
        for attempt in range(50):
            try:
                request('GET', '/', '127.0.0.1')
                break
            except (OSError, http.client.HTTPException):
                time.sleep(.1)
        run('exec', name+'-frontend', 'nginx', '-t', '-c', '/tmp/nginx.conf')
        for host in ['studydy.net', '127.0.0.1:4183', 'localhost:4183']:
            assert request('GET', '/materials/deep/link', host)[0] == 200
        for host in ['evil.example', 'studydy.net.evil.example', '']:
            for path in ['/', '/v1/session']:
                assert request('GET', path, host)[0] == 400
        headers = {h: 'synthetic-forged' for h in ['Forwarded','X-Forwarded-For','X-Forwarded-Host',
            'X-Forwarded-Port','X-Forwarded-Proto','X-Real-IP','True-Client-IP','CF-Connecting-IP',
            'CF-Connecting-IPv6','CF-Pseudo-IPv4','CF-Visitor','CF-Access-Jwt-Assertion',
            'CF-Access-Authenticated-User-Email']}
        headers.update({'Origin': 'https://wrong.example', 'Cookie': 'synthetic-cookie',
                        'Idempotency-Key': 'synthetic-intent', 'X-Material-Name': 'synthetic.pdf'})
        status, body = request('GET', '/v1/echo', 'studydy.net', headers=headers)
        assert status == 200
        received = {k.lower(): v for k,v in json.loads(body).items()}
        for h in headers:
            if h not in ['Origin','Cookie','Idempotency-Key','X-Material-Name']:
                assert h.lower() not in received, h
            else:
                assert received[h.lower()] == headers[h]
        for size in [1023, 1024, 1025]:
            assert request('POST', '/v1/upload', 'studydy.net', body=b'x'*size)[0] == (413 if size > 1024 else 200)
        print('PASS: nginx config, Host/deep links, header sanitation/preservation, upload boundary')
    finally:
        for suffix in ['-frontend','-backend']:
            subprocess.run(['docker','rm','-f',name+suffix], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run('network','rm',name)


def request(method, path, host, headers=None, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', 4183, timeout=5)
    try:
        connection.putrequest(method,path,skip_host=True)
        if host:
            connection.putheader('Host',host)
        for k,v in (headers or {}).items():connection.putheader(k,v)
        if body is not None:connection.putheader('Content-Length',str(len(body)))
        connection.endheaders(body)
        response=connection.getresponse()
        return response.status,response.read()
    finally:connection.close()


if __name__ == '__main__':main()
