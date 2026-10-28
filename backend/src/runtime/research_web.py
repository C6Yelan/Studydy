"""免費通用搜尋與公開網頁正文；候選摘要不作為教材 Evidence。"""

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
from itertools import zip_longest
import os
import re
from threading import Lock
import time
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import httpx
from trafilatura import extract, extract_metadata
from trafilatura.utils import load_html

from .errors import SourceError

_search_cache = OrderedDict()
_search_lock = Lock()
_next_search = 0.0


def canonical_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.port not in (None, 80, 443)
        ):
            return None
        host = url.hostname.encode("idna").decode().lower()
        query = [
            (key, value)
            for key, value in parse_qsl(url.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid"}
            and not (key.lower() == "source" and value.lower().startswith("rss"))
        ]
        return urlunsplit(
            (
                "https",
                host,
                quote(url.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                urlencode(query),
                "",
            )
        )
    except (ValueError, UnicodeError):
        return None


def _searx(query, page):
    global _next_search
    base = os.environ.get("STUDYDY_SEARXNG_URL", "").strip().rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError("RESEARCH_CONFIGURATION_INVALID")
    language = "zh-TW" if re.search(r"[\u3400-\u9fff]", query) else "en"
    key = (base, query, page, language)
    # 同一個公開關鍵詞／頁碼重用 10 分鐘；串行與間隔避免同時打同一批引擎。
    with _search_lock:
        cached = _search_cache.get(key)
        if cached and cached[0] > time.monotonic():
            _search_cache.move_to_end(key)
            return deepcopy(cached[1])
        delay = max(0, _next_search - time.monotonic())
        if delay > 3:
            from .research_sources import SourceAccessError

            raise SourceAccessError(
                "RESEARCH_RATE_LIMITED", retry_at=time.time() + delay
            )
        if delay:
            time.sleep(delay)
        _next_search = time.monotonic() + 3
        try:
            with httpx.Client(
                timeout=20, follow_redirects=False, trust_env=False
            ) as client:
                # 設定來自部署端；使用者與搜尋結果不能指定這個內部服務位置。
                with client.stream(
                    "POST",
                    base + "/search",
                    data={
                        "q": query,
                        "format": "json",
                        "language": language,
                        "pageno": page,
                        "categories": "general",
                    },
                ) as response:
                    if response.status_code == 429:
                        from .research_sources import _retry_delay

                        _next_search = time.monotonic() + _retry_delay(
                            response.headers.get("Retry-After"), 60
                        )
                        from .research_sources import SourceAccessError

                        raise SourceAccessError(
                            "RESEARCH_RATE_LIMITED",
                            status=429,
                            retry_at=time.time() + _next_search - time.monotonic(),
                        )
                    if response.status_code != 200:
                        raise SourceError("RESEARCH_SEARCH_FAILED")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 5 * 1024 * 1024:
                            raise SourceError("RESEARCH_SEARCH_FAILED")
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise SourceError("RESEARCH_SEARCH_FAILED")
        except (httpx.HTTPError, ValueError):
            _next_search = time.monotonic() + 30
            raise SourceError("RESEARCH_SEARCH_FAILED") from None
        warnings = data.get("unresponsive_engines", [])
        if not data["results"] and warnings:
            raise SourceError("RESEARCH_SEARCH_FAILED")
        _search_cache[key] = (time.monotonic() + (60 if warnings else 600), data)
        _search_cache.move_to_end(key)
        while len(_search_cache) > 128:
            _search_cache.popitem(last=False)
        return deepcopy(data)


def _declared_license(tree, base, *, site_policy=False):
    # 只採本頁明示的授權；文章中引用某個 CC 網址，不等於整頁可匯入。
    nodes = (
        []
        if site_policy
        else tree.xpath(
            '//head/link[contains(concat(" ",normalize-space(@rel)," ")," license ")] | //a[contains(concat(" ",normalize-space(@rel)," ")," license ")] | //head/meta[@name="license" or @property="cc:license"] | //a[@property="cc:license"]'
        )
    )
    for node in tree.xpath("//a[@href]"):
        parents = node.xpath("ancestor::*[self::p or self::footer][1]")
        statement = " ".join(" ".join(parent.itertext()) for parent in parents)
        if re.search(
            r"all (?:the )?content|text content|documentation|content on this (?:site|website)|本網站|本文|文件內容",
            statement,
            re.I,
        ) and re.search(r"licens|授權", statement, re.I):
            nodes.append(node)
    for node in nodes:
        if node.xpath("ancestor::figure | ancestor::blockquote"):
            continue
        value = urljoin(base, node.get("href") or node.get("content") or "")
        parsed = urlsplit(value)
        if parsed.hostname not in ("creativecommons.org", "www.creativecommons.org"):
            continue
        match = re.fullmatch(
            r"/licenses/(by|by-sa)/(1\.0|2\.[05]|3\.0|4\.0)(?:/(?:deed(?:\.[a-z-]+)?|legalcode(?:\.[a-z-]+)?))?/?",
            parsed.path,
            re.I,
        )
        if match:
            kind, version = match.groups()
            return (
                "CC-" + kind.upper() + "-" + version,
                "https://creativecommons.org/licenses/"
                + kind.lower()
                + "/"
                + version
                + "/",
            )
        if re.fullmatch(
            r"/publicdomain/zero/1\.0(?:/(?:deed(?:\.[a-z-]+)?|legalcode))?/?",
            parsed.path,
            re.I,
        ):
            return "CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
    raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")


def _linked_license(tree, base):
    """只沿原頁公布的同站使用條件核對文字授權，不能以政策頁自己的授權推及全站。"""
    from .research_sources import public_page

    attempts = []
    seen = set()
    for node in tree.xpath('//footer//a[@href] | //a[@rel="license"]'):
        label = " ".join(node.itertext())
        target = canonical_url(urljoin(base, node.get("href")))
        if (
            not target
            or target in seen
            or urlsplit(target).hostname != urlsplit(base).hostname
            or not re.search(r"copyright|licens|terms|版權|授權|使用條款", label, re.I)
        ):
            continue
        seen.add(target)
        if len(seen) > 2:
            break
        try:
            data, final, media = public_page(target, limit=2 * 1024 * 1024)
            if urlsplit(final).hostname != urlsplit(base).hostname:
                raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
            policy = load_html(data) if "html" in media else None
            if policy is None:
                raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
            license, license_url = _declared_license(policy, final, site_policy=True)
            text = extract(policy, include_comments=False) or ""
            attempts.append(
                {
                    "url": target,
                    "resolved_url": final,
                    "outcome": "read",
                    "method": "license_policy",
                }
            )
            return {
                "license": license,
                "license_url": license_url,
                "license_snapshot_sha256": sha256(data).hexdigest(),
                "license_text": "原頁連結的正式使用條件：" + final + "\n" + text,
                "license_attempts": attempts,
            }
        except SourceError as error:
            attempts.append(
                {
                    "url": target,
                    "outcome": "failed",
                    "error_code": str(error),
                    "http_status": getattr(error, "status", None),
                }
            )
    return {"license_attempts": attempts}


def html_document(data, final, media):
    if "html" not in media.lower():
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED")
    tree = load_html(data)
    if tree is None:
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED")
    title = " ".join(tree.xpath("//title/text()")).strip()
    if re.search(
        r"captcha|verify you are human|access denied|just a moment|確認您是人類|驗證碼",
        title,
        re.I,
    ):
        from .research_sources import SourceAccessError

        raise SourceAccessError("RESEARCH_CHALLENGE_REQUIRED", url=final)
    policy = {}
    try:
        license, license_url = _declared_license(tree, final)
    except SourceError:
        policy = _linked_license(tree, final)
        license, license_url = (
            policy.get("license", "unknown"),
            policy.get("license_url"),
        )
    metadata = extract_metadata(tree, default_url=final)
    # Wikipedia 同時放 MathML 與後備圖片；以一份 LaTeX 取代整個公式容器。
    for formula in tree.xpath(
        '//span[contains(concat(" ", normalize-space(@class), " "), " mwe-math-element ")]'
        ' | //math[@alttext and not(ancestor::span[contains(concat(" ", normalize-space(@class), " "), " mwe-math-element ")])]'
    ):
        latex = formula.get("alttext") or next(iter(formula.xpath('.//math/@alttext | .//img/@alt')), None)
        if not latex:
            continue
        for child in list(formula):
            formula.remove(child)
        formula.tag = "span"
        formula.attrib.clear()
        formula.text = "$" + latex + "$"
    # 移除網頁編輯與引註標記，保留一般數學上標與程式碼字面值。
    for node in tree.xpath(
        '//*[contains(concat(" ", normalize-space(@class), " "), " mw-editsection ")]'
        ' | //sup[contains(concat(" ", normalize-space(@class), " "), " reference ")'
        ' or contains(concat(" ", normalize-space(@class), " "), " noprint ")'
        ' or .//a[starts-with(@href, "#cite_note")]'
        ' or contains(translate(string(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "citation needed")]'
    ):
        node.drop_tree()
    for node in tree.xpath("//b | //strong"):
        node.drop_tag()
    text = extract(
        tree,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_formatting=True,
        include_links=False,
        include_images=False,
    )
    if not text or not text.strip():
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED")
    page_title = title
    title = (metadata.title if metadata else None) or title
    # 擷取結果若只有頁面題名，代表尚未取得正文；不以字數門檻判斷短文。
    normalize = lambda value: "".join(c.casefold() for c in value if c.isalnum())
    if normalize(text) and normalize(text) in {normalize(page_title), normalize(title)}:
        from .research_sources import SourceAccessError

        code = (
            "RESEARCH_AUTH_REQUIRED"
            if re.search(r"\b(?:log[ -]?in|sign[ -]?in)\b|登入|登录", title, re.I)
            else "RESEARCH_BODY_UNAVAILABLE"
        )
        raise SourceAccessError(code, url=final)
    if not title:
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    # Wikipedia 條目沒有個別作者，頁面 metadata 會誤抓頁尾的權威控制欄位，一律以網站名稱代替。
    author = (
        None if wikipedia_article(final) else metadata.author if metadata else None
    ) or urlsplit(final).hostname
    digest = sha256(data).hexdigest()
    return {
        "url": final,
        "title": title,
        "authors": author,
        "text": text,
        "license": license,
        "license_url": license_url,
        "original_sha256": digest,
        "license_snapshot_sha256": digest,
        "license_text": "來源頁面明示的授權：" + license_url if license_url else "",
        "access_url": final,
        "access_method": "html",
        "paper_body": _paper_body(tree, text),
        **policy,
        "page_identity": sha256((final + "\n" + title).encode()).hexdigest(),
    }


def _paper_body(tree, text):
    """辨識論文頁中實際可讀的正文；有 PDF 連結不代表 HTML 只有摘要。"""
    if not tree.xpath('//meta[@name="citation_title" or @name="citation_doi"]'):
        return False
    # 結構化摘要也可能有 Methods／Results；只採摘要、導覽以外且確實被擷取的段落。
    abstract_level = None
    for heading in tree.xpath("//h1 | //h2 | //h3 | //h4"):
        label = " ".join(heading.itertext()).strip()
        level = int(heading.tag[1])
        if abstract_level is not None:
            if level > abstract_level:
                continue
            abstract_level = None
        if re.fullmatch(r"abstract|summary|摘要", label, re.I):
            abstract_level = level
            continue
        if not re.match(
            r"^(?:\d+[.\s]*)?(?:introduction|background|materials? and methods|methods|methodology|results|discussion|conclusions?|緒論|前言|研究方法|結果|討論|結論)\b",
            label,
            re.I,
        ):
            continue
        if heading.xpath(
            "ancestor::nav | ancestor::aside | ancestor::header | ancestor::footer | ancestor::a"
        ):
            continue
        if any(
            re.search(
                r"abstract|summary",
                str(node.get("id", "")) + " " + str(node.get("class", "")),
                re.I,
            )
            for node in heading.iterancestors()
        ):
            continue
        # 不以論文字數、章節數或研究類型設門檻；至少有該正文段落，而非只有目錄標題。
        for sibling in heading.itersiblings():
            if sibling.tag in ("h1", "h2", "h3", "h4"):
                break
            paragraphs = [sibling] if sibling.tag == "p" else sibling.xpath(".//p")
            for paragraph in paragraphs:
                value = " ".join(" ".join(paragraph.itertext()).split())
                compact = lambda value: "".join(
                    c.casefold() for c in value if c.isalnum()
                )
                if value and compact(value) in compact(text):
                    return True
    return False


def wikipedia_article(url):
    parsed = urlsplit(url or "")
    host = parsed.hostname or ""
    path = unquote(parsed.path)
    return bool(
        re.fullmatch(r"[a-z][a-z0-9-]*(?:\.m)?\.wikipedia\.org", host)
        and path.startswith("/wiki/")
        and path[6:]
        and ":" not in path[6:]
    )


def search_wikipedia(queries, page):
    if type(page) is not int or page < 1:
        raise SourceError("RESEARCH_SEARCH_FAILED")
    rows = OrderedDict()
    warnings = []
    successes = 0
    result_sets = []
    failures = []
    for query in queries:
        try:
            data = _searx(
                "site:en.wikipedia.org " + re.sub(r"\bsite:\S+", "", query).strip(),
                page,
            )
            successes += 1
        except SourceError as error:
            failures.append(error)
            warnings.append("SearXNG")
            continue
        warnings.extend(
            "SearXNG:" + str(item[0])
            for item in data.get("unresponsive_engines", [])
            if isinstance(item, list) and item
        )
        result_sets.append(data["results"])
    # 自動搜尋只收英文條目，避免同一主題的各語言版本重複閱讀；查詢間仍交錯合併。
    for group in zip_longest(*result_sets):
        for item in group:
            if not isinstance(item, dict):
                continue
            url = canonical_url(item.get("url"))
            if (
                not url
                or not wikipedia_article(url)
                or urlsplit(url).hostname
                not in ("en.wikipedia.org", "en.m.wikipedia.org")
            ):
                continue
            providers = [
                "SearXNG:" + str(name)
                for name in item.get("engines", [])
                if isinstance(name, str)
            ]
            if url in rows:
                rows[url]["discovered_by"] = list(
                    dict.fromkeys(rows[url]["discovered_by"] + providers)
                )
                continue
            rows[url] = {
                "id": url,
                "kind": "wikipedia",
                "title": str(item.get("title") or url),
                "authors": urlsplit(url).hostname,
                "year": None,
                "doi": None,
                "url": url,
                "download_url": url,
                "license": "unknown",
                "license_url": None,
                "description": str(item.get("content") or ""),
                "description_kind": "search_snippet",
                "version": "取得時的網頁版本",
                "eligible": False,
                "reason": "待閱讀正文與核對來源。",
                "state": "candidate",
                "discovered_by": providers or ["SearXNG"],
            }
    if not successes:
        from .research_sources import SourceAccessError

        retry = [e.retry_at for e in failures if getattr(e, "retry_at", None)]
        raise SourceAccessError(
            "RESEARCH_SEARCH_FAILED", retry_at=min(retry) if retry else None
        )
    return (
        list(rows.values()),
        page + 1 if rows else None,
        list(dict.fromkeys(warnings)),
    )


def acquire_web(candidate):
    from .research_reading import read_public_document

    doc = read_public_document(candidate["download_url"])
    if not doc.get("license_url"):
        raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
    if (
        candidate.get("inspection_text_sha256")
        and sha256(doc["text"].encode()).hexdigest()
        != candidate["inspection_text_sha256"]
    ):
        raise SourceError("RESEARCH_SOURCE_CHANGED")
    if doc["page_identity"] != candidate.get("page_identity"):
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    if (
        doc["license"] != candidate["license"]
        or doc["license_url"] != candidate["license_url"]
    ):
        raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
    return document_artifact(doc, candidate)


def document_artifact(doc, candidate):
    """把已核對來源的正文固定成可匯入文字，保留原頁 hash 與正文起點。"""
    metadata = {
        key: candidate.get(key)
        for key in (
            "id",
            "title",
            "authors",
            "year",
            "doi",
            "url",
            "license",
            "license_url",
            "version",
        )
    }
    metadata.update(license=doc["license"], license_url=doc["license_url"])
    metadata.update(
        {
            key: doc[key]
            for key in ("original_sha256", "license_snapshot_sha256", "license_text")
        }
    )
    metadata.update(
        download_url=doc["url"],
        access_url=doc["access_url"],
        access_method=doc["access_method"],
        derivative_license=doc["license"],
        format="html",
    )
    if doc["access_method"] == "pdf":
        metadata["format"] = "pdf"
        return doc["content"], "application/pdf", ".pdf", metadata
    prefix = (
        doc["title"]
        + "\n來源："
        + doc["url"]
        + "\n來源網站／作者："
        + doc["authors"]
        + "\n授權："
        + doc["license"]
        + " "
        + doc["license_url"]
        + "\n變更：擷取網頁正文為文字，保留標題、表格及程式碼；未加入其他來源。\n\n"
    )
    metadata["body_offset"] = len(prefix)
    return (prefix + doc["text"]).encode(), "text/plain", ".txt", metadata
