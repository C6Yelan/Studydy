"""先取得可追溯的摘要或導讀；預覽不足時交由正文核對，不以缺摘要排除來源。"""
from html import unescape
import re
from urllib.parse import quote

from lxml import etree

from . import research_sources as sources
from .research_reading import access_failure
from .research_web import html_document

# 只限制單次初篩的預覽輸入；節錄會標明，不截斷後續正文或決定是否採用。
PREVIEW_CHARS = 4000


def plain_abstract(value):
    if not value:return ''
    tree=etree.HTML(str(value),parser=etree.HTMLParser(no_network=True))
    return ' '.join(unescape(' '.join(tree.itertext()) if tree is not None else str(value)).split())


def _result(text,kind,url,attempts,*,expanded):
    text=text.strip();excerpt=len(text)>PREVIEW_CHARS
    return {'preview':{'status':'ready' if text else 'missing','kind':kind,'url':url,
                       'excerpt':excerpt,'expanded':expanded,'attempts':attempts},
            '_preview_text':text[:PREVIEW_CHARS]}


def prepare_preview(candidate,*,expand=False):
    if candidate.get('_reading_text'):
        return _result(candidate['_reading_text'],'introduction',candidate.get('access',{}).get('url') or candidate['url'],[],expanded=True)
    description=candidate.get('description','').strip()
    if description and not expand:
        return _result(description,candidate.get('description_kind','search_description'),candidate['url'],[],expanded=False)
    attempts=[]
    if candidate.get('kind')=='paper' and candidate.get('doi'):
        url='https://api.crossref.org/works/'+quote(sources._doi(candidate['doi']),safe='')
        try:
            work=sources._json(url)['message']
            abstract=plain_abstract(work.get('abstract'))
            attempts.append({'url':url,'outcome':'read' if abstract else 'no_abstract'})
            if abstract:return _result(abstract,'abstract',url,attempts,expanded=True)
        except Exception as error:attempts.append(access_failure(error,url))
    try:
        data,final,media=sources.public_page(candidate['url'],limit=sources.MAX_BYTES)
        if data.startswith(b'%PDF-'):
            import fitz
            with fitz.open(stream=data,filetype='pdf') as pdf:
                text=pdf[0].get_text() if len(pdf) else ''
                outline='\n'.join(item[1] for item in pdf.get_toc())
            if outline:text+='\n目錄：\n'+outline
            attempts.append({'url':final,'outcome':'read','method':'first_page'})
            return _result(text,'first_page',final,attempts,expanded=True)
        tree=etree.HTML(data,parser=etree.HTMLParser(no_network=True))
        if tree is not None:
            for key,kind in [('citation_abstract','abstract'),('dc.description','description'),
                             ('description','description'),('og:description','description')]:
                values=tree.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")=$key or translate(@property,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")=$key]/@content',key=key)
                text=plain_abstract(' '.join(values))
                if text:
                    attempts.append({'url':final,'outcome':'read','method':kind})
                    return _result(text,kind,final,attempts,expanded=True)
            nodes=tree.xpath('//*[@id="abstract" or @class="abstract"]')
            if nodes:
                text=' '.join(' '.join(node.itertext()) for node in nodes)
                if text.strip():
                    attempts.append({'url':final,'outcome':'read','method':'abstract'})
                    return _result(text,'abstract',final,attempts,expanded=True)
        doc=html_document(data,final,media)
        attempts.append({'url':final,'outcome':'read','method':'introduction'})
        return _result(doc['text'],'introduction',final,attempts,expanded=True)
    except Exception as error:attempts.append(access_failure(error,candidate['url']))
    return _result(description,candidate.get('description_kind','search_description') if description else 'unavailable',
                   candidate['url'],attempts,expanded=True)


def reading_windows(text,start=0,size=12000):
    """提供完整、連續且不丟字元的定位窗口；選讀後仍以原始位置核對引用。"""
    result=[]
    while start<len(text):
        end=min(start+size,len(text))
        if end<len(text):
            boundary=text.rfind('\n',start,end)
            if boundary<=start:boundary=text.rfind(' ',start,end)
            if boundary>start:end=boundary+1
        part=text[start:end]
        headings=[line.strip() for line in part.splitlines()
                  if re.match(r'^(?:#{1,6}\s|\d+(?:\.\d+)*[.)]?\s+[A-Za-z\u4e00-\u9fff])',line.strip())]
        result.append({'start':start,'end':end,'headings':headings,
                       'opening':part[:600],'ending':part[-600:] if len(part)>600 else ''})
        start=end
    return result
