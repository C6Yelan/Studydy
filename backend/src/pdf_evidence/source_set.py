"""將各來源 PDF 的頁面接成集合閱讀順序，並保留原來源頁碼。"""

from copy import deepcopy

from .ocr_page_evidence import canonical_sha256
from .source_pdf import snapshot_pdf_source


def _ref(kind, value):
    return f"{kind}:sha256:{canonical_sha256(value)}"


def rebase_page(page, digest, number):
    result = deepcopy(page)
    page_ref = _ref("page", {"source_sha256": digest, "page_number": number})
    result.update(
        material_id=f"material:sha256:{digest}",
        material_revision=_ref("material-revision", {"source_sha256": digest}),
        page_ref=page_ref,
        page_number=number,
    )
    for block in result["evidence_blocks"]:
        region = block["locator"]["region"]
        block_id = _ref("block", {
            "page_ref": page_ref,
            "reading_order": block["reading_order"],
            "region": region,
        })
        block["block_id"] = block_id
        block["locator"] = {"page": number, "block_id": block_id, "region": region}
        block["evidence_id"] = _ref("evidence", {
            "page_ref": page_ref,
            "block_id": block_id,
            "kind": block["kind"],
            "source": block["source"],
            "text": block["text"],
            "reading_order": block["reading_order"],
            "region": region,
        })
    return result


def collect_source_set(
    inputs, binding, base, directory, settings, produced_at, report, check_cancel, extract,
):
    digest = binding["source_set_digest"]
    total_pages = len(binding["bundle"]["pages"])
    pages, excluded, ocr_calls = [], [], 0
    offset = 0
    for index, (source, item) in enumerate(zip(inputs, binding["manifest"]["items"])):
        check_cancel()
        snapshot = directory / f"source-{index}.pdf"
        checked = snapshot_pdf_source(source, snapshot)
        page_count = len(checked["page_numbers"])
        if (
            page_count != item["page_count"]
            or checked["expected_source_sha256"] != item["normalized_sha256"]
        ):
            raise ValueError("SOURCE_BINDING_INVALID")
        if base is not None and offset + page_count <= base["page_count"]:
            # 舊頁使用已驗證的 Evidence，改成目前來源集合的身分；不重跑 OCR。
            for number in range(offset + 1, offset + page_count + 1):
                prior_blocks = [
                    evidence for evidence in base["evidence"]
                    if evidence["page"] == number
                ]
                if prior_blocks:
                    page = {
                        "schema": "page-evidence/v1",
                        "evidence_blocks": [
                            {
                                "kind": evidence["kind"],
                                "source": evidence["source"],
                                "text": evidence["exact_text"],
                                "reading_order": evidence["block_order"],
                                "locator": deepcopy(evidence["source_locator"]),
                            }
                            for evidence in prior_blocks
                        ],
                    }
                    pages.append(rebase_page(page, digest, number))
                else:
                    prior = next(
                        entry for entry in base["excluded_pages"]
                        if entry["page"] == number
                    )
                    excluded.append({
                        **deepcopy(prior),
                        "page_ref": _ref("page", {
                            "source_sha256": digest,
                            "page_number": number,
                        }),
                    })
                report("evidence", number, total_pages)
        else:
            found, failed, source_ocr_calls = extract(
                snapshot, item["normalized_sha256"], checked["page_numbers"],
                settings, produced_at,
                lambda stage, completed, _total: report(
                    stage, offset + completed, total_pages
                ),
                check_cancel,
            )
            ocr_calls += source_ocr_calls
            pages.extend(
                rebase_page(page, digest, offset + page["page_number"])
                for page in found
            )
            for failure in failed:
                number = offset + failure["page"]
                excluded.append({
                    **failure,
                    "page": number,
                    "page_ref": _ref("page", {
                        "source_sha256": digest,
                        "page_number": number,
                    }),
                })
        offset += page_count
    if offset != total_pages:
        raise ValueError("SOURCE_BINDING_INVALID")
    return pages, excluded, ocr_calls
