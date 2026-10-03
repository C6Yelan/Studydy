"""受限公開來源取得。DNS 驗證後固定連線 IP，重新檢查每次重新導向。"""
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit
from .source_normalization import SourceError

MAX_BYTES=90*1024*1024
LICENSES={'cc-by':'https://creativecommons.org/licenses/', 'cc0':'https://creativecommons.org/publicdomain/zero/1.0/'}


def fetch(url, *, limit=MAX_BYTES):
    for _ in range(5):
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or any(ord(c)<33 for c in url)):
            raise SourceError('RESEARCH_URL_REJECTED')
        addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise SourceError('RESEARCH_URL_REJECTED')
        conn=http.client.HTTPSConnection(parsed.hostname,timeout=30)
        raw=socket.create_connection((addresses[0][4][0],443),timeout=30)
        try:
            conn.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=parsed.hostname)
            conn.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers={
                'User-Agent':'Studydy/1.0 educational source acquisition', 'Accept-Encoding':'identity'})
            response=conn.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location:raise SourceError('RESEARCH_DOWNLOAD_FAILED')
                url=urljoin(url,location);continue
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
    data,_,_=fetch('https://docs.python.org/3/searchindex.js',limit=12*1024*1024)
    index=json.loads(data.decode().removeprefix('Search.setIndex(').removesuffix(')'))
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


def search(query,cursor=None):
    params={'search':query,'per-page':20,'cursor':cursor or '*'}
    data,_,_=fetch('https://api.openalex.org/works?'+urlencode(params),limit=10*1024*1024)
    response=json.loads(data)
    rows=[]
    for work in response.get('results',[]):
        locations=work.get('locations',[])
        location=next((l for l in locations if l.get('license') in LICENSES and l.get('pdf_url')),None)
        best=location or work.get('best_oa_location') or work.get('primary_location') or {}
        eligible=bool(location)
        rows.append({'id':work['id'],'kind':'paper','title':work.get('title') or 'Untitled',
            'authors':', '.join(a['author']['display_name'] for a in work.get('authorships',[])[:8]),
            'year':work.get('publication_year'),'doi':work.get('doi'),'url':best.get('landing_page_url') or work.get('doi') or work['id'],
            'download_url':best.get('pdf_url'),'license':best.get('license') or 'unknown',
            'license_url':LICENSES.get(best.get('license')),'version':best.get('version') or 'unknown',
            'eligible':eligible,'reason':'可取得授權全文；下載時核對文件身分' if eligible else '尚無可核對授權的 PDF 全文',
            'state':'candidate'})
    warnings=[]
    if not cursor:
        for official in (official_search,mdn_search):
            try:rows=official(query)+rows
            except Exception:warnings.append('部分官方文件搜尋暫時不可用，可稍後重試。')
    return rows,response.get('meta',{}).get('next_cursor'),warnings


def acquire(candidate):
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
