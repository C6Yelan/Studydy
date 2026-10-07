"""公開來源開發比較；不寫入產品 DB，不作為 production 取得器。"""
import argparse
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import time
import threading
from egress import Gate
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from runtime import research_sources as sources

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = json.loads(Path(__file__).with_name('samples.json').read_text())

def digest(data):
    return hashlib.sha256(data).hexdigest()


def fetch_record(sample, method, url, directory):
    row = dict(id=sample['id'], method=method, outcome='failed', final_url=url,
               content_type=None, content_length=None, title=None, identity_match=None,
               license='unknown', license_source=None, extraction_quality='none', failure_reason=None, chain=[])
    started = time.monotonic()
    original_response = http.client.HTTPSConnection.getresponse
    # 只觀察既有 client；保留原 DNS、IP、TLS 與 redirect 檢查。
    def observe(connection):
        response = original_response(connection)
        row['chain'].append(dict(host=connection.host, status=response.status,
            location=response.getheader('Location'), content_type=response.getheader('Content-Type')))
        row['http_status'] = response.status
        row['content_type'] = response.getheader('Content-Type')
        row['declared_content_length'] = response.getheader('Content-Length')
        return response
    http.client.HTTPSConnection.getresponse = observe
    try:
        body, final, kind = sources.fetch(url, limit=8*1024*1024)
        row.update(final_url=final, content_type=kind, content_length=len(body), body_sha256=digest(body))
        text = body.decode('utf-8', errors='replace')
        (directory/f"{sample['id']}-{method}.body").write_bytes(body)
        if 'json' in kind:
            data = json.loads(text)
            work = data.get('message', {})
            row['title'] = ' '.join(work.get('title', []))
            row['identity_match'] = work.get('DOI','').lower() == sample.get('doi','').lower()
            row['extraction_quality'] = 'metadata-only'
            text = json.dumps(work, ensure_ascii=False)
        elif 'xml' in kind:
            root = ET.fromstring(body)
            row['title'] = ''.join(root.find('.//article-title').itertext())
            doi = next((e.text for e in root.iter('article-id') if e.get('pub-id-type')=='doi'), None)
            row['identity_match'] = doi == sample.get('doi')
            license_node = root.find('.//license')
            if license_node is not None:
                href = next((e.get('{http://www.w3.org/1999/xlink}href') for e in license_node.iter() if 'creativecommons.org/' in e.get('{http://www.w3.org/1999/xlink}href','')), '')
                row['license'] = sources._license(href.replace('http://','https://')) or ('unsupported:'+href if href else 'unknown')
                row['license_source'] = href or None
            body_node = root.find('.//body')
            text = '\n'.join(body_node.itertext()) if body_node is not None else ''
            row['extraction_quality'] = ('xml-body-poc' if method=='B' else 'xml-not-supported-in-production') if len(text)>100 else 'none'
        else:
            title = re.search(r'<title[^>]*>(.*?)</title>',text,re.S|re.I)
            row['title'] = re.sub('<[^>]+>','',title[1]).strip() if title else None
            row['identity_match'] = bool(sample['title'] and sample['title'].lower() in (row['title'] or '').lower())
            license_url = sample.get('license_url')
            if license_url and sample.get('license_verified') and sample['license_link_marker'] in text:
                row.update(license=sample['license'],license_source=license_url)
            try: text = sources.main_text(text)
            except sources.SourceError as error:
                row['failure_reason'] = str(error); text = ''
            row['markers_found'] = [m for m in sample['markers'] if m.lower() in text.lower()]
            row['extraction_quality'] = 'markers-present' if sample['markers'] and len(row['markers_found'])==len(sample['markers']) else 'incomplete-or-unassessed'
        if len(text)>200000: raise ValueError('TEXT_LIMIT')
        row.update(outcome='retrieved', text_length=len(text), text_sha256=digest(text.encode()))
        (directory/f"{sample['id']}-{method}.txt").write_text(text)
    except Exception as error:
        row.update(outcome='failed', failure_reason=str(error)[:400])
    finally:
        http.client.HTTPSConnection.getresponse = original_response
    row['elapsed_ms'] = round((time.monotonic()-started)*1000)
    return row


def qualify(results):
    for row in results:
        # 沒有 HTML title 的 XML／JSON 頁面尚未驗證身分，不等於 DOI 不符。
        if row['method']=='C' and not row.get('title'):
            row['identity_match']=None
            if row.get('failure_reason')=='IDENTITY_MISMATCH': row['failure_reason']=None
        row['qualified_full_text'] = (row['outcome']=='retrieved' and row.get('identity_match') is True
            and row.get('license') in ('cc-by','cc0','PSF-2.0','CC-BY-SA-2.5-or-later')
            and row.get('extraction_quality') in ('markers-present','xml-body-poc'))
        if row['outcome']=='retrieved' and not row['qualified_full_text'] and not row.get('failure_reason'):
            row['failure_reason']='IDENTITY_MISMATCH' if row.get('identity_match') is False else 'LICENSE_OR_FULL_TEXT_UNCONFIRMED'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    directory=args.output.resolve(); directory.mkdir(parents=True,exist_ok=True)
    results=[]
    licenses={}
    for sample in SAMPLES:
        if not sample.get('license_url'): continue
        url=sample['license_url']
        if url not in licenses:
            try:
                body,final,kind=sources.fetch(url,limit=2000000)
                verified=sample['license_terms_marker'].lower() in body.decode('utf-8').lower()
                name='license-'+urlsplit(url).hostname+'.body'
                (directory/name).write_bytes(body)
                licenses[url]=dict(final_url=final,verified=verified,body_sha256=digest(body),snapshot=name)
            except Exception as error: licenses[url]=dict(verified=False,error=str(error))
        sample['license_verified']=licenses[url]['verified']
    (directory/'licenses.json').write_text(json.dumps(licenses,indent=2))
    for sample in SAMPLES:
        results.append(fetch_record(sample,'A',sample['url'],directory))
        if sample.get('api_url'):
            results.append(fetch_record(sample,'B',sample['api_url'],directory))
        else:
            results.append(dict(id=sample['id'],method='B',outcome='not-available',final_url=None,content_type=None,content_length=None,title=None,identity_match=None,license='unknown',license_source=None,extraction_quality='none',elapsed_ms=0,failure_reason='No separate official API chosen for this sample'))
        results.append(dict(id=sample['id'],method='D',outcome='metadata-link-only',final_url=sample['url'],
            title=sample['title'],identity_match=None,license='unknown',license_source=None,content_type=None,
            content_length=0,extraction_quality='no-full-text',elapsed_ms=0,failure_reason='No ingestion; manifest metadata is not independently verified'))
        (directory/'direct.json').write_text(json.dumps(results,indent=2))
    pins={}
    for host in sorted({urlsplit(s['url']).hostname for s in SAMPLES}):
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Nonpublic DNS: '+host)
        pins[host]=next((a[4][0] for a in addresses if ':' not in a[4][0]),addresses[0][4][0])
    with Gate(pins) as gate:
        thread=threading.Thread(target=gate.serve_forever,daemon=True); thread.start()
        config=dict(samples=SAMPLES,pins=pins,directory=str(directory),proxy=f'http://127.0.0.1:{gate.server_address[1]}')
        (directory/'browser-input.json').write_text(json.dumps(config,indent=2))
        try:
            subprocess.run(['node',str(Path(__file__).with_name('browser.mjs')),str(directory/'browser-input.json'),str(directory/'browser.json')],check=True,timeout=360,cwd=ROOT)
        finally: gate.shutdown(); thread.join()
    results.extend(json.loads((directory/'browser.json').read_text()))
    qualify(results)
    (directory/'run.json').write_text(json.dumps(dict(completed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),samples_sha256=digest(Path(__file__).with_name('samples.json').read_bytes()),client_sha256=digest(Path(sources.__file__).read_bytes()),benchmark_sha256=digest(Path(__file__).read_bytes()),browser_sha256=digest(Path(__file__).with_name('browser.mjs').read_bytes()),egress_sha256=digest(Path(__file__).with_name('egress.py').read_bytes())),indent=2))
    (directory/'results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps([{'id':r['id'],'method':r['method'],'status':r.get('http_status'),'outcome':r['outcome'],'identity':r.get('identity_match'),'license':r.get('license'),'quality':r.get('extraction_quality'),'error':r.get('failure_reason')} for r in results],indent=2))

if __name__=='__main__': main()
