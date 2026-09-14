"""本機測試的 SSH 模型通道；不輸出教材、模型回應或憑證。"""
import base64,json,subprocess,threading,sys,shlex,os,select,time,re
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

STATE_ROOT = Path(__file__).resolve().parents[3] / '.studydy-product'
ssh_host = json.loads((STATE_ROOT / 'pod-connection.json').read_text())['ssh_host']

REMOTE = r'''
import sys,json,base64,os,urllib.request,urllib.error
allowed={'/health','/version','/v1/models','/tokenize','/v1/chat/completions'}
print('STUDYDY_MODEL_BRIDGE_READY',flush=True)
for line in sys.stdin.buffer:
 try:
  request=json.loads(line)
  if request['path'] not in allowed or request['method'] not in ('GET','POST'):raise ValueError()
  body=base64.b64decode(request['body']) if request['body'] else None
  headers={'Content-Type':'application/json'}
  key=os.environ.get('VLLM_API_KEY')
  if key:headers['Authorization']='Bearer '+key
  call=urllib.request.Request('http://127.0.0.1:18000'+request['path'],data=body,headers=headers,method=request['method'])
  try:
   with urllib.request.urlopen(call) as response:
    status=response.status;content=response.read();kind=response.headers.get('Content-Type','application/json')
  except urllib.error.HTTPError as response:
   status=response.code;content=response.read();kind=response.headers.get('Content-Type','application/json')
  result={'status':status,'body':base64.b64encode(content).decode(),'type':kind}
 except Exception:
  result={'status':503,'body':base64.b64encode(b'{"error":"MODEL_BRIDGE_UNAVAILABLE"}').decode(),'type':'application/json'}
 print('STUDYDY_MODEL_REPLY '+json.dumps(result),flush=True)
'''
command='stty raw -echo; python3 -u -c '+shlex.quote('import base64;exec(base64.b64decode('+repr(base64.b64encode(REMOTE.encode()).decode())+'))')+'\n'
assert len(command)<4000
ssh = None

def disconnect():
 global ssh
 if ssh is not None:
  ssh.terminate()
  try: ssh.wait(timeout=2)
  except subprocess.TimeoutExpired:
   ssh.kill(); ssh.wait()
  ssh = None

def connect():
 global ssh
 if ssh is not None and ssh.poll() is None: return
 disconnect()
 ssh=subprocess.Popen(['ssh','-tt','-o','BatchMode=yes','-o','ConnectTimeout=20','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','StrictHostKeyChecking=yes',ssh_host,'-i',str(Path.home()/'.ssh/id_ed25519')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 greeting=b''
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  ready,_,_=select.select([ssh.stdout],[],[],1)
  if not ready:continue
  chunk=os.read(ssh.stdout.fileno(),4096)
  if not chunk:raise RuntimeError('SSH_MODEL_CONNECTION_FAILED')
  greeting+=chunk
  if re.search(rb'root@[^\r\n]*# ',greeting):break
 else:raise RuntimeError('SSH_MODEL_PROMPT_TIMEOUT')
 ssh.stdin.write(command.encode());ssh.stdin.flush()
 greeting=b''
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  ready,_,_=select.select([ssh.stdout],[],[],1)
  if not ready:continue
  chunk=os.read(ssh.stdout.fileno(),4096)
  if not chunk:raise RuntimeError('SSH_MODEL_CONNECTION_FAILED')
  greeting+=chunk
  if greeting.rstrip().endswith(b'STUDYDY_MODEL_BRIDGE_READY'):break
 else:raise RuntimeError('SSH_MODEL_READY_TIMEOUT')
lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def do_GET(self):self.forward()
 def do_POST(self):self.forward()
 def forward(self):
  if self.path not in {'/health','/version','/v1/models','/tokenize','/v1/chat/completions'}:
   self.send_error(404);return
  try:
   size=int(self.headers.get('Content-Length',0))
   if size>1048576:raise ValueError()
   body=self.rfile.read(size)
   call=json.dumps({'method':self.command,'path':self.path,'body':base64.b64encode(body).decode()}).encode()+b'\n'
   with lock:
    try:
     connect()
     ssh.stdin.write(call);ssh.stdin.flush()
     line=ssh.stdout.readline()
     if not line.startswith(b'STUDYDY_MODEL_REPLY '):raise ValueError()
     response=json.loads(line[len(b'STUDYDY_MODEL_REPLY '):])
    except Exception:
     disconnect()
     raise RuntimeError('MODEL_BRIDGE_UNAVAILABLE') from None
   content=base64.b64decode(response['body'])
   self.send_response(response['status']);self.send_header('Content-Type',response['type']);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
  except (BrokenPipeError,ConnectionResetError):pass
  except Exception:
   self.send_error(503,'MODEL_BRIDGE_UNAVAILABLE')
print('Local model bridge listening; SSH connects only for AI requests',flush=True)
try:ThreadingHTTPServer(('127.0.0.1',18000),Handler).serve_forever()
finally:disconnect()
