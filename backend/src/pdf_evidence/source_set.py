"""來源各自保留 PDF；連續 page 是集合內的閱讀序號，由 manifest 回查真實頁碼。"""
from copy import deepcopy

from .ocr_page_evidence import canonical_sha256
from .source_pdf import snapshot_whole_document_request


def _ref(kind, value):
    return f"{kind}:sha256:{canonical_sha256(value)}"


def rebase_page(page, digest, number):
    result = deepcopy(page)
    page_ref = _ref("page", {"source_sha256": digest, "page_number": number})
    result.update(material_id=f"material:sha256:{digest}",
                  material_revision=_ref("material-revision", {"source_sha256": digest}),
                  page_ref=page_ref, page_number=number)
    for block in result["evidence_blocks"]:
        region = block["locator"]["region"]
        block_id = _ref("block", {"page_ref": page_ref, "reading_order": block["reading_order"], "region": region})
        block["block_id"] = block_id
        block["locator"] = {"page": number, "block_id": block_id, "region": region}
        block["evidence_id"] = _ref("evidence", {"page_ref": page_ref, "block_id": block_id,
            "kind": block["kind"], "source": block["source"], "text": block["text"],
            "reading_order": block["reading_order"], "region": region})
    return result


def collect_source_set(inputs, binding, base, directory, settings, produced_at, report, check_cancel, extract):
    digest = binding["source_set_digest"]
    total = len(binding["bundle"]["pages"])
    pages, excluded, calls = [], [], 0
    offset = 0
    for index, (source, item) in enumerate(zip(inputs, binding["manifest"]["items"])):
        check_cancel()
        snapshot = directory / f"source-{index}.pdf"
        checked = snapshot_whole_document_request(source, snapshot)
        count = len(checked["page_numbers"])
        if count != item["page_count"] or checked["expected_source_sha256"] != item["normalized_sha256"]:
            raise ValueError("SOURCE_BINDING_INVALID")
        if base is not None and offset + count <= base["page_count"]:
            # 重用已保存且通過 exact source 驗證的 Evidence，建立本次集合的身分；不重跑舊頁 OCR。
            for number in range(offset + 1, offset + count + 1):
                old = [e for e in base["evidence"] if e["page"] == number]
                if old:
                    page = {"schema": "page-evidence/v1", "evidence_blocks": [
                        {"kind": e["kind"], "source": e["source"], "text": e["exact_text"],
                         "reading_order": e["block_order"], "locator": deepcopy(e["source_locator"])} for e in old]}
                    pages.append(rebase_page(page, digest, number))
                else:
                    prior = next(e for e in base["excluded_pages"] if e["page"] == number)
                    excluded.append({**deepcopy(prior), "page_ref": _ref("page", {"source_sha256": digest, "page_number": number})})
                report("evidence", number, total)
        else:
            found, failed, ocr = extract(snapshot, item["normalized_sha256"], checked["page_numbers"], settings,
                produced_at, lambda stage, completed, _total: report(stage, offset + completed, total), check_cancel)
            calls += ocr
            pages.extend(rebase_page(page, digest, offset + page["page_number"]) for page in found)
            for failure in failed:
                number = offset + failure["page"]
                excluded.append({**failure, "page": number, "page_ref": _ref("page", {"source_sha256": digest, "page_number": number})})
        offset += count
    if offset != total:
        raise ValueError("SOURCE_BINDING_INVALID")
    return pages, excluded, calls
