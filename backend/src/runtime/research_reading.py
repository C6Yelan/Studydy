"""研究階段先讀公開正文，再分開判斷使用條件；失敗時核對公開替代入口。"""

from copy import deepcopy
from hashlib import sha256
from html import escape
import re
import time
import unicodedata
from urllib.parse import urljoin, urlsplit

from lxml import etree
from . import research_sources as sources
from .research_web import canonical_url, html_document
from .errors import SourceError


def access_failure(error, url):
    return {
        "url": getattr(error, "url", None) or url,
        "outcome": "failed",
        "error_code": str(error)
        if isinstance(error, SourceError)
        else "RESEARCH_DOWNLOAD_FAILED",
        "http_status": getattr(error, "status", None),
        "retry_at": getattr(error, "retry_at", None),
    }


def _feed_article(data, feed_url, article_url):
    # XML 不載入 DTD、外部 entity 或網路資源；只接受原文網址相符的那一筆。
    tree = etree.fromstring(
        data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    )
    expected = canonical_url(article_url)
    for item in tree.xpath('//*[local-name()="item" or local-name()="entry"]'):
        links = item.xpath('./*[local-name()="link"]')
        targets = [canonical_url(link.get("href") or link.text or "") for link in links]
        if expected not in targets:
            continue
        content = item.xpath('./*[local-name()="encoded" or local-name()="content"]')
        if not content:
            continue
        node = content[0]
        if len(node):
            body = "".join(
                etree.tostring(child, encoding="unicode", method="html")
                for child in node
            )
        else:
            body = node.text or ""
            if node.get("type") == "text":
                body = "<p>" + escape(body) + "</p>"
        if not body.strip():
            continue
        title = "".join(item.xpath('./*[local-name()="title"]/text()'))
        author = "".join(
            item.xpath(
                './*[local-name()="creator"]/text() | ./*[local-name()="author"]/*[local-name()="name"]/text()'
            )
        )
        html = (
            "<html><head><title>"
            + escape(title)
            + '</title><meta name="author" content="'
            + escape(author, quote=True)
            + '"></head><body><article>'
            + body
            + "</article></body></html>"
        ).encode()
        doc = html_document(html, expected, "text/html")
        doc.update(
            access_url=feed_url,
            access_method="rss",
            original_sha256=sha256(data).hexdigest(),
        )
        return doc
    raise SourceError("RESEARCH_FEED_ARTICLE_MISSING")


def _pdf_document(data, url):
    import fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            title_page_text = pdf[0].get_text() if len(pdf) else ""
            # PDF 的換行常只是排版；以原生文字區塊分段，避免把整頁當作唯一可選引用。
            text = "\n\n".join(
                block[4]
                for page in pdf
                for block in page.get_text("blocks")
                if len(block) <= 6 or block[6] == 0
            )
            title = (pdf.metadata or {}).get("title") or next(
                (line.strip() for line in text.splitlines() if line.strip()), ""
            )
    except Exception:
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED") from None
    if not text.strip():
        raise SourceError("RESEARCH_TEXT_UNAVAILABLE")
    return {
        "url": url,
        "title": title,
        "title_page_text": title_page_text,
        "authors": urlsplit(url).hostname,
        "text": text,
        "content": data,
        "license": "unknown",
        "license_url": None,
        "license_text": "",
        "original_sha256": sha256(data).hexdigest(),
        "license_snapshot_sha256": None,
        "page_identity": sha256((url + "\n" + title).encode()).hexdigest(),
        "access_url": url,
        "access_method": "pdf",
    }


def read_public_document(url, *, attempts=None, entries=None):
    attempts = attempts if attempts is not None else []
    # 每個已知入口在這次閱讀只試一次；失敗紀錄與延後時間由持久研究工作保存。
    queue = [(url, "document")] + [
        (entry["url"], entry["kind"]) for entry in entries or []
    ]
    # 連線失敗不重試同一入口；格式／身分判斷失敗仍可用公開頁閱讀路徑核對，不把它當 HTTP 失敗。
    seen = {
        canonical_url(a["url"]) or a["url"]
        for a in attempts
        if a.get("error_code")
        in (
            "RESEARCH_SOURCE_BLOCKED",
            "RESEARCH_CONNECTION_TIMEOUT",
            "RESEARCH_RATE_LIMITED",
            "RESEARCH_HTTP_FAILED",
            "RESEARCH_CHALLENGE_REQUIRED",
            "RESEARCH_AUTH_REQUIRED",
            "RESEARCH_URL_REJECTED",
        )
    }
    last_error = None
    landing = None
    declared = {}
    for target, kind in queue:
        # 原頁常公布 http 的 PDF／feed；與搜尋連結一樣先正規化為 HTTPS，仍走相同 DNS／TLS 檢查。
        target = canonical_url(target) or target
        if target in seen:
            continue
        seen.add(target)
        try:
            data, final, media = sources.public_page(
                target,
                limit=sources.MAX_BYTES
                if kind == "pdf"
                or kind == "document"
                and (target.endswith(".pdf") or "/pdf/" in target)
                else 5 * 1024 * 1024,
            )
            if kind == "rss":
                doc = _feed_article(data, final, url)
            elif data.startswith(b"%PDF-"):
                doc = _pdf_document(data, final)
                if canonical_url(target) != canonical_url(url):
                    expected = declared.get(target)
                    compact = lambda value: "".join(
                        c
                        for c in unicodedata.normalize("NFKC", value or "").casefold()
                        if c.isalnum()
                    )
                    if not expected or compact(expected) not in compact(
                        doc.get("title_page_text") or doc.get("title")
                    ):
                        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
                    doc["title"] = expected
                    if landing and landing.get("license_url"):
                        doc.update(
                            {
                                key: landing[key]
                                for key in (
                                    "license",
                                    "license_url",
                                    "license_text",
                                    "license_snapshot_sha256",
                                )
                            }
                        )
            else:
                from trafilatura.utils import load_html

                tree = load_html(data)
                if tree is not None:
                    title_nodes = tree.xpath('//meta[@name="citation_title"]/@content')
                    source_title = (
                        title_nodes[0]
                        if title_nodes
                        else " ".join(tree.xpath("(//h1)[1]//text()"))
                    ).strip()
                    source_title = (
                        source_title
                        or " ".join(tree.xpath("//title/text()"))
                        .split(" | ")[0]
                        .strip()
                    )
                    source_title = re.sub(
                        r"^(?:View of|PDF viewer[:：]?)\s+",
                        "",
                        source_title,
                        flags=re.I,
                    )
                    # 原站自己公布的全文與 feed 入口；依序嘗試，不使用搜尋摘要假裝正文。
                    fulltext = [
                        urljoin(final, href)
                        for href in tree.xpath(
                            '//meta[@name="citation_pdf_url"]/@content | //link[@rel="alternate" and @type="application/pdf"]/@href'
                        )
                    ]
                    for element in tree.xpath(
                        "//iframe[@src] | //embed[@src] | //object[@data]"
                    ):
                        href = element.get("src") or element.get("data")
                        if not href:
                            continue
                        link = urljoin(final, href)
                        if element.get(
                            "type", ""
                        ).lower() == "application/pdf" or urlsplit(
                            link
                        ).path.lower().endswith(".pdf"):
                            fulltext.append(link)
                    for element in tree.xpath("//a[@href]"):
                        label = " ".join(" ".join(element.itertext()).split())
                        if element.get(
                            "type", ""
                        ).lower() == "application/pdf" or re.search(
                            r"^PDF$|\bdownload\s+PDF\b|下載\s*PDF|下载\s*PDF",
                            label,
                            re.I,
                        ):
                            fulltext.append(urljoin(final, element.get("href")))
                    for link in fulltext:
                        link = canonical_url(link) or link
                        declared[link] = source_title
                        queue.append((link, "pdf"))
                    queue.extend(
                        (urljoin(final, href), "rss")
                        for href in tree.xpath(
                            '//link[@rel="alternate" and (@type="application/rss+xml" or @type="application/atom+xml")]/@href'
                        )
                    )
                doc = html_document(data, final, media)
                if (
                    tree is not None
                    and not fulltext
                    and not doc.get("paper_body")
                    and tree.xpath('//meta[@name="citation_title"]')
                ):
                    # 有 Word 全文附件的論文介紹頁不是正文；目前研究讀取只處理 PDF／HTML。
                    word_files = [
                        urljoin(final, link.get("href"))
                        for link in tree.xpath("//a[@href]")
                        if urlsplit(link.get("href")).path.lower().endswith(".docx")
                    ]
                    if word_files:
                        doc.update(
                            access_method="landing_page",
                            fulltext_unavailable=True,
                            fulltext_error="RESEARCH_FORMAT_UNSUPPORTED",
                        )
                if tree is not None and fulltext:
                    # 優先保留原 PDF 的圖表與排版；正文 HTML 是 PDF 不可取得時的補救。
                    doc["_html_fallback"] = bool(
                        not tree.xpath(
                            '//meta[starts-with(@name,"citation_")] | //iframe[@src] | //embed[@src] | //object[@data]'
                        )
                        and tree.xpath(
                            "//p[not(ancestor::nav or ancestor::footer)] | //pre | //table"
                        )
                    )
                    landing = doc
                    attempts.append(
                        {
                            "url": target,
                            "resolved_url": final,
                            "outcome": "read",
                            "method": "landing_page",
                        }
                    )
                    continue
            attempts.append(
                {
                    "url": target,
                    "resolved_url": final,
                    "outcome": "read",
                    "method": doc["access_method"],
                }
            )
            return doc
        except Exception as error:
            last_error = (
                error
                if isinstance(error, SourceError)
                else SourceError("RESEARCH_FORMAT_UNSUPPORTED")
            )
            attempts.append(access_failure(last_error, target))
            # 明示限流會由 fetch 遵守；其他主機的公開全文仍可繼續。
    if landing:
        if landing.pop("_html_fallback", False) or landing.get("paper_body"):
            attempts.append(
                {
                    "url": landing["url"],
                    "resolved_url": landing["access_url"],
                    "outcome": "read",
                    "method": "html",
                }
            )
            return landing
        landing.update(access_method="landing_page", fulltext_unavailable=True)
        return landing
    raise last_error or SourceError("RESEARCH_DOWNLOAD_FAILED")


def inspect_candidate(candidate, *, document=None):
    row = deepcopy(candidate)
    attempts = []
    doc = None
    importable = False
    row.pop("inspection_sha256", None)
    row.pop("inspection_text_sha256", None)
    row.update(_reading_text="", _read_offset=0, _review_chunks=[])
    if row.get("review"):
        row["review"]["phase"] = "metadata"
    if document is not None:
        data, media, suffix, metadata = document
        if media == "application/pdf":
            doc = _pdf_document(data, metadata.get("download_url") or row["url"])
        else:
            doc = {
                "text": data.decode("utf-8")[metadata.get("body_offset", 0) :],
                "access_url": metadata.get("download_url") or row["url"],
                "access_method": metadata["access_method"],
            }
        importable = True
        row["inspection_sha256"] = sha256(data).hexdigest()
        attempts.append(
            {
                "url": doc["access_url"],
                "outcome": "read",
                "method": "pdf",
                "from_snapshot": True,
            }
        )
    if doc is None and sources.arxiv_abstract_url(row["url"]):
        row["arxiv_license_checked"] = True
        try:
            row.update(sources.arxiv_metadata(row["url"]))
            if (
                row.get("screening", {}).get("decision") == "reference"
                and row["screening"].get("basis") == "importability"
            ):
                row["screening"] = {
                    "decision": "read",
                    "basis": "license_verified",
                    "reason": "同篇正式頁已確認全文使用條件，接續核對正文。",
                }
        except Exception as error:
            attempts.append(
                access_failure(error, sources.arxiv_abstract_url(row["url"]))
            )
    licensed_pdf = bool(
        row.get("download_locations")
        or sources._location(
            row.get("download_url"), row.get("license"), row.get("version")
        )
    )
    if (
        doc is None
        and not row.get("web_document")
        and (
            row["kind"] == "official"
            or row["kind"] == "paper"
            and (row.get("eligible") or licensed_pdf)
        )
    ):
        try:
            row["eligible"] = True
            data, media, suffix, metadata = sources.acquire(row, attempts=attempts)
            if media == "application/pdf":
                doc = _pdf_document(
                    data, metadata.get("download_url") or row["download_url"]
                )
            else:
                doc = {
                    "text": data.decode("utf-8")[metadata.get("body_offset", 0) :],
                    "access_url": metadata.get("download_url") or row["url"],
                    "access_method": "official"
                    if row["kind"] == "official"
                    else metadata.get("access_method", "html"),
                }
            importable = True
            for key in ("license", "license_url"):
                if metadata.get(key):
                    row[key] = metadata[key]
            row["inspection_sha256"] = sha256(data).hexdigest()
            if media == "application/pdf" or metadata.get("format") == "html":
                row["_acquired_document"] = (data, metadata)
            if not attempts:
                attempts.append(
                    {
                        "url": doc["access_url"],
                        "outcome": "read",
                        "method": doc["access_method"],
                    }
                )
        except Exception as error:
            if not attempts:
                attempts.append(
                    access_failure(error, row.get("download_url") or row["url"])
                )
    if doc is None:
        try:
            doc = read_public_document(row["url"], attempts=attempts)
            # 論文改用 HTML 入口仍須對回原論文，不能因 redirect 到另一篇 CC 文章就替換來源。
            normalize = lambda value: "".join(
                c
                for c in unicodedata.normalize("NFKC", value).casefold()
                if c.isalnum()
            )
            expected = normalize(row.get("title", ""))
            actual = normalize(doc.get("title_page_text") or doc.get("title", ""))
            same_paper = row["kind"] != "paper" or bool(expected and expected in actual)
            if not same_paper:
                attempts.append(
                    access_failure(
                        SourceError("RESEARCH_IDENTITY_UNCONFIRMED"), doc["url"]
                    )
                )
            if (
                same_paper
                and row["kind"] == "paper"
                and doc.get("paper_body")
                and not doc.get("license_url")
                and row.get("license") in sources.LICENSES
                and row.get("license_url")
            ):
                # 已確認同篇正式原頁正文；沿用該版本索引的明示授權，不要求網頁重複標同一個 rel。
                doc.update(
                    license=row["license"],
                    license_url=row["license_url"],
                    license_snapshot_sha256=None,
                    license_text="索引記錄的同篇版本授權：" + row["license_url"],
                )
            if doc.get("license_url") and same_paper:
                row.update(
                    {
                        key: doc[key]
                        for key in (
                            "title",
                            "authors",
                            "license",
                            "license_url",
                            "page_identity",
                        )
                    }
                )
                row.update(download_url=doc["url"], web_document=True)
                importable = True
                if not doc.get("fulltext_unavailable"):
                    from .research_web import document_artifact

                    data, media, suffix, metadata = document_artifact(doc, row)
                    row["inspection_sha256"] = sha256(data).hexdigest()
                    row["_acquired_document"] = (data, metadata)
        except SourceError:
            pass
    row["eligible"] = importable
    if doc:
        attempts.extend(doc.get("license_attempts", []))
    row["access"] = {
        "attempts": candidate.get("access", {}).get("attempts", []) + attempts,
        "status": "read" if doc else "unavailable",
    }
    if doc:
        if doc["access_method"] == "pdf" and doc.get("original_sha256"):
            row["inspection_sha256"] = doc["original_sha256"]
        row["_reading_text"] = doc["text"]
        row["inspection_text_sha256"] = sha256(doc["text"].encode()).hexdigest()
        row["access"].update(
            method=doc["access_method"],
            url=doc["access_url"],
            characters=len(doc["text"]),
        )
        if doc.get("title"):
            row["access"]["actual_title"] = doc["title"]
            if row["title"] == row["url"]:
                row["title"] = doc["title"]
                row["authors"] = doc.get("authors") or row.get("authors")
        row["reason"] = (
            "已取得正文並確認使用條件。"
            if importable
            else "已讀取公開內容；全文匯入條件尚未確認，可查看原始來源。"
        )
        if doc.get("fulltext_unavailable"):
            row["access"]["fulltext_unavailable"] = True
            row["reason"] = (
                "全文只有 Word 檔，目前補充研究尚未支援讀取此格式；可至原始來源下載。"
                if doc.get("fulltext_error") == "RESEARCH_FORMAT_UNSUPPORTED"
                else "已讀取公開頁面，但該頁公布的全文入口未能取得；請查看存取紀錄。"
            )
            # 介紹頁即使可讀或有授權，也不是尚未取得的全文，不能交給正文驗收。
            row["access"]["status"] = "unavailable"
            row["_reading_text"] = ""
            row["eligible"] = False
            future = [
                a["retry_at"]
                for a in attempts
                if a.get("retry_at") and a["retry_at"] > time.time()
            ]
            if future:
                row["access"]["retry_at"] = min(future)
    else:
        row["reason"] = (
            "已嘗試原始來源與已知公開入口，仍未取得正文；保留逐次結果供接續查找。"
        )
        future = [
            a["retry_at"]
            for a in attempts
            if a.get("retry_at") and a["retry_at"] > time.time()
        ]
        if future:
            row["access"]["retry_at"] = min(future)
    if row["access"]["status"] == "unavailable":
        actual_failure = any(
            a.get("outcome") == "failed"
            and (
                a.get("http_status") is not None
                or a.get("error_code") != "RESEARCH_RATE_LIMITED"
            )
            for a in attempts
        )
        failures = candidate.get("access", {}).get("failed_acquisitions", 0) + int(
            actual_failure
        )
        row["access"]["failed_acquisitions"] = failures
        # 同站尚在冷卻、未發出請求不算來源再失敗；真正重試仍取不到就停止。
        if not row["access"].get("retry_at") or failures >= 2:
            row["access"]["exhausted"] = True
            row["access"].pop("retry_at", None)
    return row
