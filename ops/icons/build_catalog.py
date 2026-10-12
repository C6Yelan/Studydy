from pathlib import Path
import argparse,json,re,hashlib,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description="Build offline concept-card catalog from public Tabler and CC-CEDICT sources")
parser.add_argument("--tabler",type=Path,required=True)
parser.add_argument("--cedict",type=Path,required=True)
args=parser.parse_args()

def norm(s):return re.sub(r'\s+',' ',s.lower().replace('-',' ').replace('_',' ')).strip()
def senses(gloss):
 out=[];start=0;depth=0
 for i,c in enumerate(gloss):
  if c=='(':depth+=1
  elif c==')':depth=max(0,depth-1)
  elif c==';' and depth==0:out.append(gloss[start:i].strip());start=i+1
 out.append(gloss[start:].strip());return out

def entries():
 for n,line in enumerate(args.cedict.read_text().splitlines(),1):
  m=re.match(r'^(\S+) (\S+) \[([^\]]+)\] /(.+)/$',line)
  if m:yield n,m.group(1),m.group(2),[s for g in m.group(4).split('/') for s in senses(g)]

def main():
 icons=json.loads((args.tabler/'icons.json').read_text())
 english={};rows=set()
 def alias(term,icon,cost,origin,q=''):
  term=norm(term)
  if not term:return
  rows.add((term,icon,cost,origin,q))
 def en(term,icon,cost,origin):
  term=norm(term);english.setdefault(term,[]).append((icon,cost,origin));alias(term,icon,cost,origin)
 for name,item in icons.items():
  en(name,name,0,'Tabler canonical name')
  if name.startswith(('device-','building-','brand-')):
   en(name.split('-',1)[1],name,.3,'Tabler namespace-separated name')
 # 僅選官方 tags 明列的名詞別名，並核對實際內容。
 useful={'device-desktop':['computer','pc','workstation'],'device-laptop':['laptop','notebook'],'device-mobile':['phone','smartphone','cellphone','mobile-phone'],'zoom':['magnifier','magnifying-glass'],'mail':['email','e-mail'],'paperclip':['attachment'],'test-pipe':['test-tube'],'network':['internet','lan'],'brand-apple':['macos','ios'],'fridge':['refrigerator'],'bike':['bicycle']}
 for icon,words in useful.items():
  tags={norm(str(v)) for v in icons[icon]['tags']}
  for word in words:
   assert norm(word) in tags,(icon,word)
   en(word,icon,.4,'Tabler explicit noun tag: '+word)
 for word,icon in [('電腦','device-desktop'),('行動裝置','device-mobile'),('服務主機','server')]:alias(word,icon,.1,'Existing Studydy object vocabulary')
 for n,trad,simp,glosses in entries():
  for i,raw in enumerate(glosses):
   if raw.startswith(('to ','CL:','see ','variant of ','old variant')):continue
   qualifiers=re.findall(r'\(([^)]*)\)',raw);lemma=re.sub(r'\([^)]*\)','',raw).strip();lemma=re.sub(r'^(?:a|an|the) ','',lemma)
   # 保留詞義限定與出處，避免把地區用字直接當成不同語意。
   keys=[norm(lemma)]
   general=re.sub(r'^(?:clinical|digital|electronic|handheld|optical|portable)\s+','',norm(lemma))
   if general!=norm(lemma):keys.append(general);qualifiers.append('dictionary subtype: '+lemma)
   for q in qualifiers:keys.extend([norm(lemma+' '+q),norm(q+' '+lemma)])
   for key in dict.fromkeys(keys):
    for icon,cost,origin in english.get(key,[]):
     for term in sorted({trad,simp}):alias(term,icon,cost+i*.45,'CC-CEDICT line '+str(n)+' / '+raw,' '.join(qualifiers))
 # 素材進入產品前只接收靜態 SVG 幾何，不允許外部 URL、script 或事件屬性。
 allowed={'path':{'d'},'circle':{'cx','cy','r'},'rect':{'x','y','width','height','rx','ry'},'line':{'x1','x2','y1','y2'},'polyline':{'points'},'polygon':{'points'},'ellipse':{'cx','cy','rx','ry'}}
 shapes={}
 for name,item in icons.items():
  root=ET.parse(args.tabler/'icons/outline'/f'{name}.svg').getroot()
  nodes=[]
  for node in root:
   tag=node.tag.rsplit('}',1)[-1]
   if node.attrib.get('stroke')=='none':continue
   if tag not in allowed or set(node.attrib)-allowed[tag]-{'fill','opacity'} or node.attrib.get('fill','none') not in {'none','currentColor'} or node.attrib.get('opacity','1') not in {'1','.5'}:raise ValueError((name,tag,node.attrib))
   nodes.append([tag,node.attrib])
  shapes[name]=nodes
 words=sorted({w for _,trad,simp,_ in entries() for w in (trad,simp)})
 output=ROOT/'frontend/src/features/concept-cards/icons'
 output.mkdir(parents=True,exist_ok=True)
 def write(name,data):
  (output/name).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n')
 write('catalog.json',{'icons':{name:item['category'] for name,item in icons.items()},'aliases':sorted(rows),'words':' '.join(words)})
 write('shapes.json',shapes)
 write('sources.json',{'tabler_version':json.loads((args.tabler/'package.json').read_text())['version'],'tabler_metadata_sha256':hashlib.sha256((args.tabler/'icons.json').read_bytes()).hexdigest(),'cedict_sha256':hashlib.sha256(args.cedict.read_bytes()).hexdigest(),'icons':len(icons),'aliases':len(rows),'licenses':{'geometry':'MIT (Tabler Icons)','dictionary':'CC-BY-SA-4.0 (CC-CEDICT / MDBG)'}})
 (ROOT/'frontend/public/licenses/tabler-icons.txt').write_bytes((args.tabler/'LICENSE').read_bytes())
 print(f"Generated {len(icons)} icons, {len(rows)} aliases")
if __name__=='__main__':main()
