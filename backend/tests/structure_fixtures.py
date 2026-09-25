"""純合成的來源集合 binding，讓結構單元測試使用正式 v1；不冒充產品來源。"""

from knowledge_map.structure import build_structure_draft, finalize_knowledge_structure
from pdf_evidence.ocr_page_evidence import canonical_sha256


def build_knowledge_structure(context, state, **kwargs):
    draft = build_structure_draft(context, state, **kwargs)
    source_id = "00000000-0000-4000-8000-000000000001"
    digest = kwargs["source_sha256"]
    manifest = {"schema": "source-set/v1", "items": [{
        "source_id": source_id, "original_name": "Synthetic.pdf",
        "original_sha256": digest, "normalized_sha256": digest,
    }]}
    bundle = {
        "schema": "bundle-manifest/v1",
        "source_set_digest": digest,
        "processing_policy": "source-boundary-incremental/v1",
        "source_names": ["Synthetic.pdf"],
        "pages": [
            {"page": page, "source_id": source_id, "normalized_page": page}
            for page in range(1, draft["page_count"] + 1)
        ],
    }
    return finalize_knowledge_structure(draft, {
        "schema": "structure-input-binding/v1",
        "source_set_id": source_id,
        "source_set_digest": digest,
        "bundle_manifest_sha256": canonical_sha256(bundle),
        "manifest": manifest,
        "bundle": bundle,
        "base_revision": None,
    })
