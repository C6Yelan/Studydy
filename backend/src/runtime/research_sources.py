"""受限公開來源取得。DNS 驗證後固定連線 IP，重新檢查每次重新導向。"""

from collections import OrderedDict
from datetime import UTC
from email.utils import parsedate_to_datetime
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
import unicodedata
from urllib.parse import urlencode, urljoin, urlsplit, quote
from .errors import SourceError

MAX_BYTES = 90 * 1024 * 1024
LICENSES = {
    "cc-by": "https://creativecommons.org/licenses/",
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
}


_rate_limited_until = {}
_host_next = {}
_host_lock = Lock()
_PUBLIC_CACHE = OrderedDict()
_public_cache_lock = Lock()
_USER_AGENT = "Studydy/1.0 (+https://studydy.net; educational source acquisition)"


class SourceAccessError(SourceError):
    """保留本次存取結果；單頁拒絕不代表整個網站不可用。"""

    def __init__(self, code, *, url=None, status=None, retry_at=None):
        super().__init__(code)
        self.url, self.status, self.retry_at = url, status, retry_at


def _retry_delay(value, default):
    try:
        if value.strip().isdigit():
            return max(1, int(value))
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(1, date.timestamp() - time.time())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return default


def _reserve_host(host):
    with _host_lock:
        now = time.monotonic()
        if _rate_limited_until.get(host, 0) > now:
            raise SourceAccessError(
                "RESEARCH_RATE_LIMITED",
                retry_at=time.time() + _rate_limited_until[host] - now,
            )
        delay = max(0, _host_next.get(host, 0) - now)
        # 長冷卻不佔用 worker 等待，保留明確的限流結果供稍後重試。
        _host_next[host] = now + delay + 2
        if len(_host_next) > 1024:
            stale = sorted(_host_next, key=_host_next.get)[:512]
            for key in stale:
                if max(_host_next[key], _rate_limited_until.get(key, 0)) < now:
                    _host_next.pop(key, None)
                    _rate_limited_until.pop(key, None)
    if delay:
        time.sleep(delay)
        with _host_lock:
            now = time.monotonic()
            if _rate_limited_until.get(host, 0) > now:
                raise SourceAccessError(
                    "RESEARCH_RATE_LIMITED",
                    retry_at=time.time() + _rate_limited_until[host] - now,
                )


def _cooldown(host, status, retry_after=None):
    # 只有站方限流或明示 Retry-After 才暫停同站；403、逾時與單頁 5xx 留給研究工作嘗試其他入口。
    if status != 429 and not retry_after:
        return None
    delay = _retry_delay(retry_after, 60)
    with _host_lock:
        _rate_limited_until[host] = max(
            _rate_limited_until.get(host, 0), time.monotonic() + delay
        )
    return time.time() + delay


def public_page(url, *, limit=5 * 1024 * 1024):
    # 只快取無認證的公開正文／授權頁；私密教材與帶 key 的 API 請求不走這裡。
    with _public_cache_lock:
        cached = _PUBLIC_CACHE.get(url)
        if cached and cached[0] > time.monotonic():
            if len(cached[1][0]) > limit:
                raise SourceError("MATERIAL_TOO_LARGE")
            _PUBLIC_CACHE.move_to_end(url)
            return cached[1]
    result = fetch(url, limit=limit)
    if len(result[0]) <= 2 * 1024 * 1024:
        with _public_cache_lock:
            _PUBLIC_CACHE[url] = (time.monotonic() + 600, result)
            _PUBLIC_CACHE.move_to_end(url)
            while len(_PUBLIC_CACHE) > 16:
                _PUBLIC_CACHE.popitem(last=False)
    return result


_semantic_lock = Lock()
_semantic_next = 0


def fetch(url, *, limit=MAX_BYTES, headers=None):
    original_host = urlsplit(url).hostname
    for _ in range(5):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or any(ord(c) < 33 for c in url)
        ):
            raise SourceError("RESEARCH_URL_REJECTED")
        _reserve_host(parsed.hostname)
        try:
            addresses = socket.getaddrinfo(
                parsed.hostname, 443, type=socket.SOCK_STREAM
            )
        except OSError:
            raise SourceAccessError("RESEARCH_DNS_FAILED", url=url) from None
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise SourceError("RESEARCH_URL_REJECTED")
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=30)
        raw = None
        try:
            # 同一個已驗證 DNS 回應的其他位址可處理 IPv6／路由失敗，不改用代理或任意 IP。
            for index, address in enumerate(addresses):
                try:
                    raw = socket.create_connection((address[4][0], 443), timeout=30)
                    conn.sock = ssl.create_default_context().wrap_socket(
                        raw, server_hostname=parsed.hostname
                    )
                    break
                except OSError:
                    if raw:
                        raw.close()
                    if index == len(addresses) - 1:
                        raise
            request_headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
            if parsed.hostname == original_host and headers:
                request_headers.update(headers)
            conn.request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                headers=request_headers,
            )
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise SourceError("RESEARCH_DOWNLOAD_FAILED")
                url = urljoin(url, location)
                if url.startswith("http://"):
                    url = "https://" + url[7:]
                continue
            if response.status in (429, 503):
                retry_at = _cooldown(
                    parsed.hostname, response.status, response.getheader("Retry-After")
                )
                raise SourceAccessError(
                    "RESEARCH_RATE_LIMITED" if retry_at else "RESEARCH_HTTP_FAILED",
                    url=url,
                    status=response.status,
                    retry_at=retry_at,
                )
            if response.status == 403:
                retry_at = _cooldown(
                    parsed.hostname, 403, response.getheader("Retry-After")
                )
                raise SourceAccessError(
                    "RESEARCH_SOURCE_BLOCKED", url=url, status=403, retry_at=retry_at
                )
            if response.status != 200:
                raise SourceAccessError(
                    "RESEARCH_HTTP_FAILED", url=url, status=response.status
                )
            size = response.getheader("Content-Length")
            if size and int(size) > limit:
                raise SourceError("MATERIAL_TOO_LARGE")
            data = response.read(limit + 1)
            if not data or len(data) > limit:
                raise SourceError("MATERIAL_TOO_LARGE")
            return data, url, response.getheader("Content-Type", "")
        except (OSError, http.client.HTTPException) as error:
            raise SourceAccessError(
                "RESEARCH_CONNECTION_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "RESEARCH_CONNECTION_FAILED",
                url=url,
            ) from None
        finally:
            conn.close()
            if raw:
                raw.close()
    raise SourceError("RESEARCH_URL_REJECTED")


def normalized_title(value):
    """文件身分比對共用 Unicode 正規化；各入口仍保留完整或包含比對規則。"""
    return "".join(
        c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum()
    )


def _doi(value):
    value = (value or "").strip().lower()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)


def _license(value):
    if not isinstance(value, str):
        return None
    value = value.lower().strip()
    if value == "ccby":
        value = "cc-by"
    if value in LICENSES:
        return value
    parsed = urlsplit(value)
    if parsed.hostname not in ("creativecommons.org", "www.creativecommons.org"):
        return None
    if re.fullmatch(r"/licenses/by/[1-4]\.0(?:/legalcode)?/?", parsed.path):
        return "cc-by"
    if re.fullmatch(r"/publicdomain/zero/1\.0(?:/legalcode)?/?", parsed.path):
        return "cc0"
    return None


def _location(url, license, version, landing=None, license_url=None):
    license = _license(license)
    # 索引可能只有已授權的論文頁，PDF 由該頁正式公布；不能因 pdf_url 為空就丟掉授權與位置。
    url = url or landing
    if not license or not isinstance(url, str):
        return None
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.startswith("https://"):
        return None
    return {
        "download_url": url,
        "license": license,
        "license_url": license_url or LICENSES[license],
        "version": {"vor": "publishedVersion", "am": "acceptedVersion"}.get(
            version, version
        )
        or "unknown",
        "url": landing or url,
    }


def _paper(identity, title, authors, year, doi, url, locations, provider):
    locations = [x for x in locations if x]
    best = locations[0] if locations else {}
    from .research_web import canonical_url

    landing = best.get("url") or url
    return {
        "id": identity,
        "kind": "paper",
        "title": title or "Untitled",
        "authors": authors,
        "year": year,
        "doi": "https://doi.org/" + _doi(doi) if doi else None,
        "url": canonical_url(landing) or landing,
        "download_url": best.get("download_url"),
        "license": best.get("license", "unknown"),
        "license_url": best.get("license_url"),
        "version": best.get("version", "unknown"),
        "eligible": bool(locations),
        "reason": "有授權全文連結；取得時確認可下載並核對文件身分"
        if locations
        else "目前沒有符合匯入條件的授權全文連結",
        "state": "candidate",
        "download_locations": locations,
        "discovered_by": [provider],
    }


def _json(url, *, headers=None):
    data, _, _ = fetch(
        url, limit=10 * 1024 * 1024, **({"headers": headers} if headers else {})
    )
    return json.loads(data)


def openalex_search(query, cursor):
    key = os.environ.get("STUDYDY_OPENALEX_API_KEY", "").strip()
    # 語意檢索使用完整研究問題；只找已有開放全文位置的論文，不以標題字面排名。
    page = int(cursor)
    response = _json(
        "https://api.openalex.org/works?"
        + urlencode(
            {
                "search.semantic": query,
                "per-page": 20,
                "page": page,
                "filter": "is_oa:true,has_abstract:true",
            }
        ),
        headers={"Authorization": "Bearer " + key} if key else None,
    )
    rows = []
    for work in response.get("results", []):
        locations = [
            _location(
                l.get("pdf_url"),
                l.get("license"),
                l.get("version"),
                l.get("landing_page_url"),
            )
            for l in work.get("locations", [])
        ]
        best = work.get("best_oa_location") or work.get("primary_location") or {}
        rows.append(
            _paper(
                work["id"],
                work.get("title"),
                ", ".join(
                    a["author"]["display_name"] for a in work.get("authorships", [])[:8]
                ),
                work.get("publication_year"),
                work.get("doi"),
                best.get("landing_page_url") or work.get("doi") or work["id"],
                locations,
                "OpenAlex",
            )
        )
        index = work.get("abstract_inverted_index") or {}
        rows[-1]["description"] = " ".join(
            word
            for _, word in sorted(
                (position, word)
                for word, positions in index.items()
                for position in positions
            )
        )
        rows[-1]["description_kind"] = "abstract"
    # semantic search 最多回傳 50 篇，不能沿普通搜尋 cursor 無限翻頁。
    count = min(50, response.get("meta", {}).get("count", 0))
    return rows, page + 1 if rows and page * 20 < count else None


def semantic_scholar_search(query, offset):
    key = os.environ.get("STUDYDY_SEMANTIC_SCHOLAR_API_KEY", "").strip()
    global _semantic_next
    with _semantic_lock:
        delay = _semantic_next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _semantic_next = time.monotonic() + 1.1
        response = _json(
            "https://api.semanticscholar.org/graph/v1/paper/search?"
            + urlencode(
                {
                    "query": query,
                    "limit": 20,
                    "offset": offset,
                    "fields": "title,abstract,authors,year,externalIds,url,openAccessPdf",
                }
            ),
            headers={"x-api-key": key} if key else None,
        )
    rows = []
    for work in response.get("data", []):
        pdf = work.get("openAccessPdf") or {}
        rows.append(
            _paper(
                work["url"],
                work["title"],
                ", ".join(a["name"] for a in work.get("authors", [])[:8]),
                work.get("year"),
                work.get("externalIds", {}).get("DOI"),
                work["url"],
                [_location(pdf.get("url"), pdf.get("license"), "unknown", work["url"])],
                "Semantic Scholar",
            )
        )
        rows[-1].update(
            description=work.get("abstract") or "", description_kind="abstract"
        )
    return rows, response.get("next")


def candidate_key(candidate):
    # arXiv 在學術索引與一般網頁常使用不同 ID；合併同一版本，不能把 v1 默默換成 v2。
    for value in (
        candidate.get("url", ""),
        candidate.get("doi", ""),
        candidate.get("id", ""),
    ):
        match = re.search(
            r"(?:arxiv\.org/(?:abs|pdf|html)/|10\.48550/arxiv\.)(\d{4}\.\d{4,5}(?:v\d+)?)",
            value or "",
            re.I,
        )
        if match:
            return ("arxiv", match.group(1).lower())
    if candidate.get("doi"):
        return ("doi", _doi(candidate["doi"]))
    from .research_web import canonical_url

    return ("url", canonical_url(candidate.get("url", "")) or candidate["id"])


def merge_candidates(existing, incoming):
    # 保留舊 id、勾選與已取得來源；後續搜尋不可替換已下載版本或來源綁定。
    merged = deepcopy(existing)
    known = {candidate_key(c): c for c in merged}
    ids = {c["id"]: c for c in merged}
    for row in incoming:
        key = candidate_key(row)
        if key not in known and row["id"] not in ids:
            row = deepcopy(row)
            merged.append(row)
            known[key] = row
            ids[row["id"]] = row
            continue
        kept = known.get(key) or ids[row["id"]]
        kept["discovered_by"] = list(
            dict.fromkeys(kept.get("discovered_by", []) + row.get("discovered_by", []))
        )
        kept["research_topics"] = list(
            dict.fromkeys(
                kept.get("research_topics", []) + row.get("research_topics", [])
            )
        )
        # 自動補查再次找到同一筆，不得清除已停止取得的紀錄並重新追逐。
        if kept.get("access", {}).get("exhausted"):
            continue
        if row.get("description") and (
            not kept.get("description")
            or row.get("description_kind") == "abstract"
            and kept.get("description_kind") != "abstract"
        ):
            kept["description"] = row["description"]
            kept["description_kind"] = row.get("description_kind", "search_description")
            if kept.get("review", {}).get("phase") != "body" and not kept.get(
                "_read_offset"
            ):
                for field in ("preview", "_preview_text", "screening", "review"):
                    kept.pop(field, None)
        if kept.get("state") != "candidate":
            continue
        locations = kept.setdefault("download_locations", [])
        seen = {(l["download_url"], l["version"]) for l in locations}
        for location in row.get("download_locations", []):
            if (location["download_url"], location["version"]) not in seen:
                locations.append(deepcopy(location))
                seen.add((location["download_url"], location["version"]))
        if (
            not kept["eligible"]
            and row["eligible"]
            and kept.get("review", {}).get("relevance") != "unrelated"
        ):
            for field in (
                "eligible",
                "reason",
                "download_url",
                "license",
                "license_url",
                "version",
                "url",
                "kind",
                "title",
                "authors",
                "page_identity",
            ):
                if field in row:
                    kept[field] = row[field]
            if kept.get("access"):
                kept.pop("access", None)
                for field in (
                    "_reading_text",
                    "_read_offset",
                    "_review_chunks",
                    "inspection_sha256",
                    "inspection_text_sha256",
                    "_document_snapshot",
                    "_acquired_document",
                ):
                    kept.pop(field, None)
                if kept.get("review"):
                    kept["review"]["phase"] = "metadata"
    return merged


def search(query, cursor=None, *, channel="papers"):
    if channel not in ("wikipedia", "papers"):
        raise SourceError("REQUEST_INVALID")
    if cursor:
        try:
            state = json.loads(cursor)
        except (ValueError, TypeError):
            raise SourceError("REQUEST_INVALID") from None
        if not isinstance(state, dict):
            raise SourceError("REQUEST_INVALID")
    else:
        state = {"openalex": 1} if channel == "papers" else {}
        if channel == "papers" and os.environ.get("STUDYDY_SEMANTIC_SCHOLAR_API_KEY"):
            state["semantic_scholar"] = 0
    queries = list(
        dict.fromkeys(line.strip() for line in query.splitlines() if line.strip())
    )[:3]
    primary = queries[0] if queries else query
    web_enabled = bool(os.environ.get("STUDYDY_SEARXNG_URL", "").strip())
    if not cursor and web_enabled and channel == "wikipedia":
        state["web"] = 1
    providers = {
        "openalex": openalex_search,
        "semantic_scholar": semantic_scholar_search,
    }
    rows = []
    warnings = []
    next_state = {}
    successes = 0
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            name: pool.submit(providers[name], primary, position)
            for name, position in state.items()
            if name in providers
        }
        web_job = None
        if web_enabled and "web" in state:
            from .research_web import search_wikipedia

            web_job = pool.submit(search_wikipedia, queries, state["web"])
        for name, job in jobs.items():
            try:
                found, next_cursor = job.result()
                rows.extend(found)
                successes += 1
                if next_cursor is not None:
                    next_state[name] = next_cursor
            except Exception as error:
                warnings.append(name)
                next_state[name] = state[name]
                failures.append(
                    {
                        "provider": name,
                        "error_code": str(error)
                        if isinstance(error, SourceError)
                        else "RESEARCH_SEARCH_FAILED",
                        "retry_at": getattr(error, "retry_at", None),
                    }
                )
        if web_job:
            try:
                found, next_page, partial = web_job.result()
                rows.extend(found)
                successes += 1
                warnings.extend(partial)
                if next_page is not None:
                    next_state["web"] = next_page
            except Exception as error:
                warnings.append("SearXNG")
                next_state["web"] = state["web"]
                failures.append(
                    {
                        "provider": "web",
                        "error_code": str(error)
                        if isinstance(error, SourceError)
                        else "RESEARCH_SEARCH_FAILED",
                        "retry_at": getattr(error, "retry_at", None),
                    }
                )
    if failures:
        next_state["_failures"] = failures
    if not successes:
        future = [
            failure["retry_at"] for failure in failures if failure.get("retry_at")
        ]
        raise SourceAccessError(
            "RESEARCH_SEARCH_FAILED", retry_at=min(future) if future else None
        )
    return (
        merge_candidates([], rows),
        json.dumps(next_state, separators=(",", ":")) if next_state else None,
        warnings,
    )


def _unpaywall_locations(candidate):
    email = os.environ.get("STUDYDY_UNPAYWALL_EMAIL", "").strip()
    doi = _doi(candidate.get("doi"))
    if not email or not doi:
        return []
    data = _json(
        "https://api.unpaywall.org/v2/"
        + quote(doi, safe="")
        + "?"
        + urlencode({"email": email})
    )
    if _doi(data.get("doi")) != doi:
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    return [
        l
        for item in data.get("oa_locations", [])
        if (
            l := _location(
                item.get("url_for_pdf"),
                item.get("license"),
                item.get("version"),
                item.get("url_for_landing_page"),
            )
        )
    ]


def arxiv_abstract_url(url):
    parsed = urlsplit(url or "")
    if parsed.hostname not in ("arxiv.org", "www.arxiv.org"):
        return None
    match = re.fullmatch(
        r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?", parsed.path
    )
    return "https://arxiv.org/abs/" + match.group(1) if match else None


def arxiv_metadata(url):
    """只從同篇 arXiv 正式摘要頁核對授權，不把可下載 PDF 當作使用許可。"""
    from trafilatura.utils import load_html

    abstract = arxiv_abstract_url(url)
    if not abstract:
        return None
    data, final, media = public_page(abstract, limit=2 * 1024 * 1024)
    if arxiv_abstract_url(final) != abstract or "html" not in media.lower():
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    tree = load_html(data)
    if tree is None:
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED")
    title = tree.xpath('//meta[@name="citation_title"]/@content')
    pdf = tree.xpath('//meta[@name="citation_pdf_url"]/@content')
    if not title or not pdf:
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    identity = abstract.rsplit("/", 1)[-1]
    advertised = arxiv_abstract_url(pdf[0])
    if not advertised or re.sub(r"v\d+$", "", advertised) != re.sub(
        r"v\d+$", "", abstract
    ):
        raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    licenses = tree.xpath(
        '//div[contains(concat(" ",normalize-space(@class)," ")," abs-license ")]/a[@title="Rights to this article"]/@href'
    )
    license_url = next((value for value in licenses if _license(value)), None)
    if not license_url:
        raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
    return {
        "kind": "paper",
        "title": title[0],
        "authors": ", ".join(tree.xpath('//meta[@name="citation_author"]/@content')),
        "download_url": "https://arxiv.org/pdf/" + identity,
        "license": _license(license_url),
        "license_url": license_url,
        "version": "arxiv:" + identity,
        "web_document": False,
        "arxiv_license_checked": True,
        "license_snapshot_sha256": hashlib.sha256(data).hexdigest(),
        "license_text": "arXiv 正式摘要頁 "
        + abstract
        + " 明示 Rights to this article："
        + license_url,
    }


def acquire(candidate, *, attempts=None):
    if not candidate.get("eligible"):
        raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
    if candidate.get("arxiv_license_checked"):
        current = arxiv_metadata(candidate["url"])
        if not current or any(
            current[key] != candidate.get(key)
            for key in ("title", "download_url", "license", "license_url", "version")
        ):
            raise SourceError("RESEARCH_SOURCE_CHANGED")
        candidate.update(current)
    if candidate["kind"] == "web" or candidate.get("web_document"):
        from .research_web import acquire_web

        return acquire_web(candidate)
    original_version = candidate.get("version", "unknown")
    primary = _location(
        candidate.get("download_url"),
        candidate.get("license"),
        original_version,
        candidate.get("url"),
        candidate.get("license_url"),
    )
    locations = ([primary] if primary else []) + candidate.get("download_locations", [])
    seen = set()
    last_error = None

    # 嘗試已知的同版本公開位置；不以固定次數阻擋仍可核對的來源。
    def attempt(options, *, same_version=True):
        nonlocal last_error
        for location in options:
            url = location.get("download_url")
            if (
                not url
                or url in seen
                or (location.get("version") == original_version) != same_version
            ):
                continue
            seen.add(url)
            try:
                data, media, suffix, metadata = _acquire_location(
                    {**candidate, **location}, attempts=attempts
                )
                if attempts is not None:
                    attempts.append(
                        {
                            "url": url,
                            "resolved_url": metadata.get("download_url", url),
                            "outcome": "read",
                            "method": "pdf"
                            if media == "application/pdf"
                            else metadata["access_method"],
                            "version": location.get("version"),
                        }
                    )
                if location.get("version") != original_version:
                    metadata["alternate_version_of"] = original_version
                candidate.update(location)
                return data, media, suffix, metadata
            except Exception as error:
                last_error = (
                    error
                    if isinstance(error, SourceError)
                    else SourceError("RESEARCH_DOWNLOAD_FAILED")
                )
                if attempts is not None:
                    attempts.append(
                        {
                            "url": url,
                            "outcome": "failed",
                            "error_code": str(last_error),
                            "http_status": getattr(error, "status", None),
                            "retry_at": getattr(error, "retry_at", None),
                        }
                    )
        return None

    result = attempt(locations)
    if result:
        return result
    try:
        extra = _unpaywall_locations(candidate)
    except Exception:
        extra = []
    result = attempt(extra)
    if result:
        return result
    # 第一次閱讀前可採索引明列的作者稿／其他版本，並記錄實際版本；已讀原檔不能被替換。
    if (
        not candidate.get("inspection_sha256")
        and candidate.get("review", {}).get("phase") != "body"
        and not candidate.get("arxiv_license_checked")
        and not candidate.get("source_id")
    ):
        result = attempt(locations + extra, same_version=False)
        if result:
            return result
    raise last_error or SourceError("RESEARCH_DOWNLOAD_FAILED")


def _acquire_location(candidate, *, attempts=None):
    url = candidate["download_url"]
    data, final, media = public_page(url, limit=MAX_BYTES)
    metadata = {
        k: candidate.get(k)
        for k in (
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
    for key in ("license_snapshot_sha256", "license_text"):
        if candidate.get(key):
            metadata[key] = candidate[key]
    metadata.update(
        download_url=final, original_sha256=hashlib.sha256(data).hexdigest()
    )
    if candidate["kind"] == "paper" and not data.startswith(b"%PDF-"):
        from .research_reading import read_public_document

        document = read_public_document(url, attempts=attempts)
        if document.get("fulltext_unavailable"):
            raise SourceError("RESEARCH_BODY_UNAVAILABLE")
        if document["access_method"] != "pdf":
            if not document.get("paper_body"):
                raise SourceError("RESEARCH_BODY_UNAVAILABLE")
            from .research_web import document_artifact

            if normalized_title(candidate["title"]) not in normalized_title(
                document["title"]
            ):
                raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
            # 此入口與授權來自同篇、同版本 metadata，仍以原頁正文核對身分。
            if not document.get("license_url"):
                document.update(
                    license=candidate["license"],
                    license_url=candidate["license_url"],
                    license_snapshot_sha256=None,
                    license_text="索引記錄的同篇版本授權：" + candidate["license_url"],
                )
            acquired = document_artifact(document, candidate)
            if (
                candidate.get("inspection_sha256")
                and hashlib.sha256(acquired[0]).hexdigest()
                != candidate["inspection_sha256"]
            ):
                raise SourceError("RESEARCH_SOURCE_CHANGED")
            return acquired
        data = document["content"]
        final = document["access_url"]
        media = "application/pdf"
        metadata.update(
            download_url=final, original_sha256=hashlib.sha256(data).hexdigest()
        )
    if (
        candidate.get("inspection_sha256")
        and metadata["original_sha256"] != candidate["inspection_sha256"]
    ):
        raise SourceError("RESEARCH_SOURCE_CHANGED")
    if candidate["license"] not in LICENSES or not data.startswith(b"%PDF-"):
        raise SourceError("RESEARCH_LICENSE_UNCONFIRMED")
    import fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            expected = normalized_title(candidate["title"] or "")
            title_matches = bool(
                expected
                and normalized_title((doc.metadata or {}).get("title") or "") == expected
            )
            first_text = ""
            for page in doc:
                blocks = page.get_text("blocks")
                if not first_text:
                    # 題名可能與期刊／系列名稱共用一個區塊；仍逐行核對完整題名，不做模糊比對。
                    title_blocks = [
                        normalized_title(line)
                        for block in blocks
                        if block[6] == 0
                        for line in block[4].splitlines()
                        if line.strip()
                    ]
                    first_text = " ".join(
                        block[4] for block in blocks if block[6] == 0
                    ).strip()
                    # 排版可能把完整題名切成相鄰區塊；仍須逐字匹配，不以共有術語或相似比例認定身分。
                    for start in range(len(title_blocks)):
                        joined = ""
                        for part in title_blocks[start:]:
                            joined += part
                            if expected and joined == expected:
                                title_matches = True
                            if len(joined) >= len(expected):
                                break
                if expected and any(
                    normalized_title(block[4]) == expected
                    for block in blocks
                    if block[6] == 0
                ):
                    title_matches = True
            doi = _doi(candidate.get("doi"))
            # DOI 要出現在文首，不能拿參考文獻中的 DOI 認作本篇；標題則核對完整 metadata／文字區塊。
            doi_matches = bool(doi and doi in first_text.casefold())
        if not (doi_matches or title_matches):
            raise SourceError("RESEARCH_IDENTITY_UNCONFIRMED")
    except SourceError:
        raise
    except Exception:
        raise SourceError("RESEARCH_FORMAT_UNSUPPORTED") from None
    return data, "application/pdf", ".pdf", metadata
