"""受限公開來源取得。DNS 驗證後固定連線 IP，重新檢查每次重新導向。"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import os
import time
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit, quote
from .source_normalization import SourceError

MAX_BYTES=90*1024*1024
LICENSES={'cc-by':'https://creativecommons.org/licenses/', 'cc0':'https://creativecommons.org/publicdomain/zero/1.0/'}


_rate_limited_until={}
_python_index=None
_semantic_lock=Lock()
_semantic_next=0


def fetch(url, *, limit=MAX_BYTES, headers=None):
    original_host=urlsplit(url).hostname
    for _ in range(5):
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or any(ord(c)<33 for c in url)):
            raise SourceError('RESEARCH_URL_REJECTED')
        if _rate_limited_until.get(parsed.hostname,0)>time.monotonic():raise SourceError('RESEARCH_RATE_LIMITED')
        addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise SourceError('RESEARCH_URL_REJECTED')
        conn=http.client.HTTPSConnection(parsed.hostname,timeout=30)
        raw=socket.create_connection((addresses[0][4][0],443),timeout=30)
        try:
            conn.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=parsed.hostname)
            request_headers={'User-Agent':'Studydy/1.0 educational source acquisition','Accept-Encoding':'identity'}
            if parsed.hostname==original_host and headers:request_headers.update(headers)
            conn.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers=request_headers)
            response=conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location:raise SourceError('RESEARCH_DOWNLOAD_FAILED')
                url=urljoin(url,location);continue
            if response.status==429:
                delay=response.getheader('Retry-After','60')
                _rate_limited_until[parsed.hostname]=time.monotonic()+min(3600,max(1,int(delay))) if delay.isdigit() else time.monotonic()+60
                raise SourceError('RESEARCH_RATE_LIMITED')
            if response.status==403:raise SourceError('RESEARCH_SOURCE_BLOCKED')
            if response.status!=200:raise SourceError('RESEARCH_DOWNLOAD_FAILED')
            size=response.getheader('Content-Length')
            if size and int(size)>limit:raise SourceError('MATERIAL_TOO_LARGE')
            data=response.read(limit+1)
            if not data or len(data)>limit:raise SourceError('MATERIAL_TOO_LARGE')
            return data,url,response.getheader('Content-Type','')
        finally:
            conn.close();raw.close()
    raise SourceError('RESEARCH_URL_REJECTED')


class MainText(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if self.depth:self.depth+=1 if tag not in {'br','hr','img','input','meta','link','wbr','source'} else 0
        elif tag=='main' or a.get('role')=='main':self.depth=1
        if tag in {'script','style'}:self.hidden+=1
        if self.depth and tag in {'td','th'}:self.parts.append('\t')
        if self.depth and tag in {'p','div','section','pre','li','h1','h2','h3','tr','br'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'br','hr','img','input','meta','link','wbr','source'}:return
        if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
        if self.depth:self.depth-=1
        if tag in {'p','pre','li','h1','h2','h3','tr'}:self.parts.append('\n')
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        self.handle_endtag(tag)
    def handle_data(self,data):
        if self.depth and not self.hidden:self.parts.append(data)


def main_text(html):
    parser=MainText();parser.feed(html)
    text=''.join(parser.parts).strip()
    if len(text)<100:raise SourceError('RESEARCH_FORMAT_UNSUPPORTED')
    return text


def official_search(query):
    global _python_index
    if _python_index is None or time.monotonic()-_python_index[0]>86400:
        data,_,_=fetch('https://docs.python.org/3/searchindex.js',limit=12*1024*1024)
        _python_index=(time.monotonic(),json.loads(data.decode().removeprefix('Search.setIndex(').removesuffix(')')))
    index=_python_index[1]
    words=set(re.findall(r'[a-z][a-z0-9]+',query.lower()))-{'the','and','for','with','about','of','to','a','in'}
    scores={}
    for word in words:
        for field,weight in [('titleterms',8),('terms',1)]:
            ids=index.get(field,{}).get(word,[])
            if isinstance(ids,int):ids=[ids]
            for identity in ids:scores[identity]=scores.get(identity,0)+weight
    results=[]
    for i in sorted(scores,key=lambda x:(-scores[x],x))[:15]:
        path=index['docnames'][i]
        if not path.startswith(('tutorial/','library/','howto/')):continue
        title=unescape(re.sub(r'<[^>]+>', '', index['titles'][i]))
        url='https://docs.python.org/3/'+path+'.html'
        results.append({'id':url,'kind':'official','title':title,'authors':'Python Software Foundation',
            'year':None,'doi':None,'url':url,'download_url':url,'license':'PSF-2.0','license_url':'https://docs.python.org/3/license.html',
            'version':'Python 3 documentation; exact bytes recorded on acquisition',
            'eligible':True,'reason':'官方文件，保留 PSF 授權與版權聲明','state':'candidate'})
    return results


def mdn_search(query):
    data,_,_=fetch('https://developer.mozilla.org/api/v1/search?'+urlencode({'q':query,'locale':'en-US'}),limit=2000000)
    rows=[]
    for item in json.loads(data).get('documents',[]):
        path=item.get('mdn_url','')
        if not path.startswith('/en-US/docs/'):continue
        url='https://developer.mozilla.org'+path
        rows.append({'id':url,'kind':'official','title':item['title'],'authors':'Mozilla Contributors',
            'year':None,'doi':None,'url':url,'download_url':url,'license':'CC-BY-SA-2.5-or-later',
            'license_url':'https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines/Attrib_copyright_license',
            'version':'MDN Web Docs; exact bytes recorded on acquisition','eligible':True,
            'reason':'保留作者與來源；衍生文字適用相同授權','state':'candidate'})
    return rows


def _doi(value):
    value=(value or '').strip().lower()
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',value)


def _license(value):
    if not isinstance(value,str):return None
    value=value.lower().strip()
    if value=='ccby':value='cc-by'
    if value in LICENSES:return value
    parsed=urlsplit(value)
    if parsed.hostname not in ('creativecommons.org','www.creativecommons.org'):return None
    if re.fullmatch(r'/licenses/by/[1-4]\.0(?:/legalcode)?/?',parsed.path):return 'cc-by'
    if re.fullmatch(r'/publicdomain/zero/1\.0(?:/legalcode)?/?',parsed.path):return 'cc0'
    return None


def _location(url,license,version,landing=None,license_url=None):
    license=_license(license)
    if not license or not isinstance(url,str):return None
    if url.startswith('http://'):url='https://'+url[7:]
    if not url.startswith('https://'):return None
    return {'download_url':url,'license':license,'license_url':license_url or LICENSES[license],
            'version':{'vor':'publishedVersion','am':'acceptedVersion'}.get(version,version) or 'unknown','url':landing or url}


def _paper(identity,title,authors,year,doi,url,locations,provider):
    locations=[x for x in locations if x]
    best=locations[0] if locations else {}
    return {'id':identity,'kind':'paper','title':title or 'Untitled','authors':authors,'year':year,
        'doi':'https://doi.org/'+_doi(doi) if doi else None,'url':best.get('url') or url,
        'download_url':best.get('download_url'),'license':best.get('license','unknown'),
        'license_url':best.get('license_url'),'version':best.get('version','unknown'),
        'eligible':bool(locations),'reason':'有授權全文連結；取得時確認可下載並核對文件身分' if locations else '目前沒有符合匯入條件的授權全文連結',
        'state':'candidate','download_locations':locations,'discovered_by':[provider]}


def _json(url,*,headers=None):
    data,_,_=fetch(url,limit=10*1024*1024,**({'headers':headers} if headers else {}))
    return json.loads(data)


def openalex_search(query,cursor):
    key=os.environ.get('STUDYDY_OPENALEX_API_KEY','').strip()
    response=_json('https://api.openalex.org/works?'+urlencode({'search':query,'per-page':20,'cursor':cursor}),
                   headers={'Authorization':'Bearer '+key} if key else None)
    rows=[]
    for work in response.get('results',[]):
        locations=[_location(l.get('pdf_url'),l.get('license'),l.get('version'),l.get('landing_page_url')) for l in work.get('locations',[])]
        best=work.get('best_oa_location') or work.get('primary_location') or {}
        rows.append(_paper(work['id'],work.get('title'),', '.join(a['author']['display_name'] for a in work.get('authorships',[])[:8]),
            work.get('publication_year'),work.get('doi'),best.get('landing_page_url') or work.get('doi') or work['id'],locations,'OpenAlex'))
    return rows,response.get('meta',{}).get('next_cursor')


def crossref_search(query,offset):
    response=_json('https://api.crossref.org/works?'+urlencode({'query':query,'rows':20,'offset':offset,
        'filter':'has-license:true,has-full-text:true',
        'select':'DOI,title,author,published,license,link,URL,type'}))['message']
    rows=[]
    for work in response.get('items',[]):
        # 只採已生效、且與全文版本相同的 CC 授權；TDM 或 similarity-checking 不當作開放全文。
        locations=[]
        for link in work.get('link',[]):
            if link.get('content-type')!='application/pdf' or link.get('intended-application')=='similarity-checking':continue
            for license in work.get('license',[]):
                start=license.get('start',{}).get('timestamp')
                if start is not None and start>time.time()*1000:continue
                if license.get('content-version')!=link.get('content-version'):continue
                locations.append(_location(link.get('URL'),license.get('URL'),link.get('content-version'),work.get('URL'),license.get('URL')))
        parts=work.get('published',{}).get('date-parts',[[]])
        rows.append(_paper('https://doi.org/'+_doi(work['DOI']),(work.get('title') or ['Untitled'])[0],
            ', '.join(' '.join(filter(None,[a.get('given'),a.get('family')])) for a in work.get('author',[])[:8]),
            parts[0][0] if parts and parts[0] else None,work['DOI'],work.get('URL') or 'https://doi.org/'+work['DOI'],locations,'Crossref'))
    next_offset=offset+len(response.get('items',[]))
    return rows,next_offset if next_offset<min(response.get('total-results',0),10000) else None


def semantic_scholar_search(query,offset):
    key=os.environ.get('STUDYDY_SEMANTIC_SCHOLAR_API_KEY','').strip()
    global _semantic_next
    with _semantic_lock:
        delay=_semantic_next-time.monotonic()
        if delay>0:time.sleep(delay)
        _semantic_next=time.monotonic()+1.1
        response=_json('https://api.semanticscholar.org/graph/v1/paper/search?'+urlencode({'query':query,'limit':20,'offset':offset,
            'fields':'title,authors,year,externalIds,url,openAccessPdf'}),headers={'x-api-key':key} if key else None)
    rows=[]
    for work in response.get('data',[]):
        pdf=work.get('openAccessPdf') or {}
        rows.append(_paper(work['url'],work['title'],', '.join(a['name'] for a in work.get('authors',[])[:8]),
            work.get('year'),work.get('externalIds',{}).get('DOI'),work['url'],
            [_location(pdf.get('url'),pdf.get('license'),'unknown',work['url'])],'Semantic Scholar'))
    return rows,response.get('next')


def candidate_key(candidate):
    return ('doi',_doi(candidate['doi'])) if candidate.get('doi') else ('id',candidate['id'])


def merge_candidates(existing,incoming):
    # 保留舊 id、勾選與已取得來源；後續搜尋不可替換已下載版本或來源綁定。
    merged=deepcopy(existing);known={candidate_key(c):c for c in merged};ids={c['id']:c for c in merged}
    for row in incoming:
        key=candidate_key(row)
        if key not in known and row['id'] not in ids:
            row=deepcopy(row);merged.append(row);known[key]=row;ids[row['id']]=row;continue
        kept=known.get(key) or ids[row['id']]
        kept['discovered_by']=list(dict.fromkeys(kept.get('discovered_by',[])+row.get('discovered_by',[])))
        if kept.get('state')!='candidate':continue
        locations=kept.setdefault('download_locations',[])
        seen={(l['download_url'],l['version']) for l in locations}
        for location in row.get('download_locations',[]):
            if (location['download_url'],location['version']) not in seen:
                locations.append(deepcopy(location));seen.add((location['download_url'],location['version']))
        if not kept['eligible'] and row['eligible']:
            for field in ('eligible','reason','download_url','license','license_url','version','url'):kept[field]=row[field]
    return merged


_SEARCH_STOP={'the','and','for','with','about','of','to','a','in','an','how','what','is','are',
    'introduction','introductory','beginner','beginners','tutorial','advanced','overview','fundamentals','basic','basics'}


def _terms(text):return set(re.findall(r'[a-z0-9]+',text.lower()))-_SEARCH_STOP


def rank_candidates(query,rows):
    terms=_terms(query)
    if not terms:return rows
    def matches(row):return len(terms&_terms(row['title']))
    # 官方站內搜尋常只命中一個泛詞；多詞查詢至少要有兩個標題詞符合才列出。
    rows=[r for r in rows if r['kind']!='official' or matches(r)>=min(2,len(terms))]
    return sorted(rows,key=lambda r:(not r['eligible'],-matches(r)))


def search(query,cursor=None):
    # cursor 仍是 opaque string；既有 OpenAlex cursor 只接續原本來源，不重跑首頁。
    if cursor:
        try:state=json.loads(cursor)
        except (ValueError,TypeError):state={'openalex':cursor}
        if not isinstance(state,dict):state={'openalex':cursor}
    else:
        state={'openalex':'*','crossref':0}
        if os.environ.get('STUDYDY_SEMANTIC_SCHOLAR_API_KEY'):state['semantic_scholar']=0
    providers={'openalex':openalex_search,'crossref':crossref_search,'semantic_scholar':semantic_scholar_search}
    rows=[];warnings=[];next_state={};successes=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={name:pool.submit(providers[name],query,position) for name,position in state.items() if name in providers}
        official_jobs=[] if cursor else [pool.submit(official,query) for official in (official_search,mdn_search)]
        for name,job in jobs.items():
            try:
                found,next_cursor=job.result();rows.extend(found);successes+=1
                if next_cursor is not None:next_state[name]=next_cursor
            except Exception:
                warnings.append(name);next_state[name]=state[name]
        for index,job in enumerate(official_jobs):
            try:rows.extend(job.result());successes+=1
            except Exception:warnings.append(('Python','MDN')[index])
    if not successes:raise SourceError('RESEARCH_SEARCH_FAILED')
    return rank_candidates(query,merge_candidates([],rows)),json.dumps(next_state,separators=(',',':')) if next_state else None,warnings


def _unpaywall_locations(candidate):
    email=os.environ.get('STUDYDY_UNPAYWALL_EMAIL','').strip()
    doi=_doi(candidate.get('doi'))
    if not email or not doi:return []
    data=_json('https://api.unpaywall.org/v2/'+quote(doi,safe='')+'?'+urlencode({'email':email}))
    if _doi(data.get('doi'))!=doi:raise SourceError('RESEARCH_IDENTITY_UNCONFIRMED')
    return [l for item in data.get('oa_locations',[]) if (l:=_location(item.get('url_for_pdf'),item.get('license'),item.get('version'),item.get('url_for_landing_page')))]


def acquire(candidate):
    if not candidate.get('eligible'):raise SourceError('RESEARCH_LICENSE_UNCONFIRMED')
    if candidate['kind']=='official':return _acquire_location(candidate)
    original_version=candidate.get('version','unknown')
    primary=_location(candidate.get('download_url'),candidate.get('license'),original_version,candidate.get('url'),candidate.get('license_url'))
    locations=([primary] if primary else [])+candidate.get('download_locations',[])
    seen=set();last_error=None
    # 一次最多試三個相同版本的授權位置；不把預印本默默替換成出版版本。
    def attempt(options):
        nonlocal last_error
        for location in options:
            url=location.get('download_url')
            if not url or url in seen or len(seen)>=3 or location.get('version')!=original_version:continue
            seen.add(url)
            try:
                data,media,suffix,metadata=_acquire_location({**candidate,**location})
                candidate.update(location)
                return data,media,suffix,metadata
            except Exception as error:
                last_error=error if isinstance(error,SourceError) else SourceError('RESEARCH_DOWNLOAD_FAILED')
        return None
    result=attempt(locations)
    if result:return result
    if len(seen)<3:
        try:extra=_unpaywall_locations(candidate)
        except Exception:extra=[]
        result=attempt(extra)
        if result:return result
    raise last_error or SourceError('RESEARCH_DOWNLOAD_FAILED')


def _acquire_location(candidate):
    if not candidate.get('eligible'):raise SourceError('RESEARCH_LICENSE_UNCONFIRMED')
    url=candidate['download_url']
    data,final,media=fetch(url)
    metadata={k:candidate.get(k) for k in ('id','title','authors','year','doi','url','license','license_url','version')}
    metadata.update(download_url=final,original_sha256=hashlib.sha256(data).hexdigest())
    if candidate['kind']=='official' and urlsplit(url).hostname=='developer.mozilla.org':
        if urlsplit(final).hostname!='developer.mozilla.org' or not urlsplit(final).path.startswith('/en-US/docs/'):
            raise SourceError('RESEARCH_URL_REJECTED')
        html=data.decode('utf-8')
        if '/docs/MDN/Writing_guidelines/Attrib_copyright_license' not in html:raise SourceError('RESEARCH_LICENSE_UNCONFIRMED')
        text=main_text(html)
        license_data,_,_=fetch(candidate['license_url'],limit=2000000)
        metadata['license_text']=main_text(license_data.decode('utf-8'))
        metadata['license_snapshot_sha256']=hashlib.sha256(license_data).hexdigest()
        metadata['derivative_license']='CC-BY-SA-2.5-or-later'
        text=(candidate['title']+'\n來源：'+final+'\n作者：Mozilla Contributors\n'
            +'授權：CC BY-SA 2.5 或更新版本；程式範例依 MDN code sample 條款。\n'
            +'變更：由官方 HTML 擷取正文為純文字；教學整理／翻譯仍保留來源與相同文字授權。\n'
            +'授權說明：'+candidate['license_url']+'\n\n'+text)
        return text.encode(),'text/plain','.txt',metadata
    if candidate['kind']=='official':
        if urlsplit(final).hostname!='docs.python.org' or not urlsplit(final).path.startswith('/3/'):
            raise SourceError('RESEARCH_URL_REJECTED')
        text=main_text(data.decode('utf-8'))
        license_data,license_final,_=fetch('https://docs.python.org/3/license.html',limit=2000000)
        license_text=main_text(license_data.decode('utf-8'))
        metadata['license_snapshot_sha256']=hashlib.sha256(license_data).hexdigest()
        metadata['license_text']=license_text
        text=(candidate['title']+'\n來源：'+final+'\n作者：Python Software Foundation\n'
            +'變更說明：擷取官方 HTML 的正文為純文字供教材分析；未加入其他來源。\n\n'+text
            +'\n\nCopyright © Python Software Foundation. All rights reserved.\n授權：PSF-2.0；完整授權與版權聲明保存於此來源的取得紀錄：'+license_final)
        return text.encode(),'text/plain','.txt',metadata
    if candidate['license'] not in LICENSES or not data.startswith(b'%PDF-'):
        raise SourceError('RESEARCH_LICENSE_UNCONFIRMED')
    import fitz
    try:
        with fitz.open(stream=data,filetype='pdf') as doc:
            text=' '.join(doc[i].get_text() for i in range(min(3,len(doc)))).lower()
        doi=(candidate.get('doi') or '').lower().removeprefix('https://doi.org/')
        words=re.findall(r'[a-z0-9]{3,}',candidate['title'].lower())
        # metadata 的版本必須對得回實際文件；不足時保留失敗，不猜測。
        if not ((doi and doi in text) or (len(words)>=3 and sum(w in text for w in words)>=len(words)*0.85)):
            raise SourceError('RESEARCH_IDENTITY_UNCONFIRMED')
    except SourceError:raise
    except Exception:raise SourceError('RESEARCH_FORMAT_UNSUPPORTED') from None
    return data,'application/pdf','.pdf',metadata
