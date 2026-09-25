"""選用 SSH shell 模型通道；只轉送固定 HTTP 路由，連線失敗不重播請求。"""

import base64
import json
import os
from pathlib import Path
import select
import shlex
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROUTES = {'/health', '/version', '/v1/models', '/tokenize', '/v1/chat/completions'}
READY = b'STUDYDY_MODEL_BRIDGE_READY'
REPLY = b'STUDYDY_MODEL_REPLY '
REMOTE = r'''
import sys,json,base64,os,urllib.request,urllib.error
allowed={'/health','/version','/v1/models','/tokenize','/v1/chat/completions'}
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
print('STUDYDY_MODEL_BRIDGE_READY',flush=True)
for line in sys.stdin.buffer:
 try:
  request=json.loads(line)
  if request['path'] not in allowed or request['method'] not in ('GET','POST'):raise ValueError()
  body=base64.b64decode(request['body']) if request['body'] else None
  headers={'Content-Type':'application/json'}
  key=os.environ.get('VLLM_API_KEY')
  if key:headers['Authorization']='Bearer '+key
  call=urllib.request.Request('http://127.0.0.1:'+str(MODEL_PORT)+request['path'],data=body,headers=headers,method=request['method'])
  try:
   response=http.open(call)
  except urllib.error.HTTPError as error:
   response=error
  with response:
   content=response.read(1024*1024+1)
   if len(content)>1024*1024:raise ValueError()
   result={'status':response.status,'body':base64.b64encode(content).decode(),'type':response.headers.get('Content-Type','application/json')}
 except Exception:
  result={'status':503,'body':base64.b64encode(b'{"error":"MODEL_BRIDGE_UNAVAILABLE"}').decode(),'type':'application/json'}
 print('STUDYDY_MODEL_REPLY '+json.dumps(result),flush=True)
'''


class SSHBridge:
    def __init__(self, host: str, port: int, model_port: int = 18000):
        if not host or host.startswith('-') or any(char.isspace() for char in host) or not 1 <= port <= 65535 or not 1 <= model_port <= 65535:
            raise ValueError('SSH_SETTINGS_INVALID')
        self.host, self.port = host, port
        self.model_port = model_port
        self.process = None
        self.lock = threading.Lock()

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def connect(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.process = subprocess.Popen([
            'ssh', '-tt', '-i', '/run/secrets/model_key', '-p', str(self.port),
            '-o', 'UserKnownHostsFile=/run/secrets/known_hosts',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=30',
            '-o', 'ServerAliveCountMax=3', self.host,
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        encoded = base64.b64encode(REMOTE.encode()).decode()
        script = f'import base64;MODEL_PORT={self.model_port};exec(base64.b64decode({encoded!r}))'
        # 不依 root 提示字元判斷登入；已編碼的指令不會把 READY marker 回顯成成功。
        command = 'stty raw -echo; exec python3 -u -c ' + shlex.quote(script) + '\n'
        self.process.stdin.write(command.encode())
        self.process.stdin.flush()
        deadline = time.monotonic() + 30
        greeting = b''
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], 1)
            if not ready:
                continue
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                break
            greeting += chunk
            if greeting.rstrip().endswith(READY):
                return
            if len(greeting) > 65536:
                break
        raise RuntimeError('SSH_MODEL_CONNECTION_FAILED')

    def forward(self, path, method, body):
        if path not in ROUTES or method not in {'GET', 'POST'} or len(body) > 1024 * 1024:
            raise ValueError('MODEL_BRIDGE_REQUEST_INVALID')
        request = {'path': path, 'method': method, 'body': base64.b64encode(body).decode()}
        with self.lock:
            try:
                self.connect()
                self.process.stdin.write(json.dumps(request).encode() + b'\n')
                self.process.stdin.flush()
                line = self.process.stdout.readline(2 * 1024 * 1024)
                if not line.startswith(REPLY):
                    raise ValueError()
                response = json.loads(line[len(REPLY):])
                status, kind = response['status'], response['type']
                if type(status) is not int or not 100 <= status <= 599 or not isinstance(kind, str) or any(char in kind for char in '\r\n'):
                    raise ValueError()
                return status, base64.b64decode(response['body'], validate=True), kind
            except Exception:
                self.close()
                raise RuntimeError('MODEL_BRIDGE_UNAVAILABLE') from None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if self.path not in ROUTES:
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 <= size <= 1024 * 1024:
                self.send_error(413)
                return
            status, content, kind = self.server.bridge.forward(self.path, self.command, self.rfile.read(size))
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_error(503, 'MODEL_BRIDGE_UNAVAILABLE')


def main():
    bridge = None
    try:
        bridge = SSHBridge(
            os.environ['STUDYDY_SSH_HOST'], int(os.environ.get('STUDYDY_SSH_PORT', '22')),
            int(os.environ.get('STUDYDY_SSH_MODEL_PORT', '18000')),
        )
        if not all(Path(path).is_file() for path in ('/run/secrets/model_key', '/run/secrets/known_hosts')):
            raise ValueError('SSH_SETTINGS_INVALID')
        def stop(*_):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        with ThreadingHTTPServer(('0.0.0.0', 18000), Handler) as server:
            server.bridge = bridge
            server.serve_forever()
    except Exception:
        print('MODEL_BRIDGE_CONFIGURATION_OR_SERVER_FAILED', flush=True)
        raise SystemExit(1) from None
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == '__main__':
    main()
