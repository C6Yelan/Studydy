"""純合成的來源集合 binding，讓結構單元測試使用正式 v1；不冒充產品來源。"""

from knowledge_map.structure import build_structure_draft, finalize_knowledge_structure
from content_identity import canonical_sha256


def build_knowledge_structure(context, state, **kwargs):
    draft = build_structure_draft(context, state, **kwargs)
    source_id = "00000000-0000-4000-8000-000000000001"
    digest = kwargs["source_sha256"]
    pages = context.get("source_pages") or [
        {"page": page, "source_id": source_id, "normalized_page": page}
        for page in range(1, draft["page_count"] + 1)
    ]
    sources = list(dict.fromkeys(page["source_id"] for page in pages))
    manifest = {"schema": "source-set/v2", "items": [{
        "source_id": identity, "original_name": "Synthetic.pdf",
        "original_sha256": digest, "normalized_sha256": digest,
        **({"transcript": context["spoken_sources"][identity]}
           if identity in context.get("spoken_sources", {}) else {}),
    } for identity in sources]}
    bundle = {
        "schema": "bundle-manifest/v2",
        "source_set_digest": digest,
        "processing_policy": "source-boundary-incremental/v1",
        "source_names": ["Synthetic.pdf"] * len(sources),
        "pages": pages,
    }
    return finalize_knowledge_structure(draft, {
        "schema": "structure-input-binding/v2",
        "source_set_id": source_id,
        "source_set_digest": digest,
        "bundle_manifest_sha256": canonical_sha256(bundle),
        "manifest": manifest,
        "bundle": bundle,
        "base_revision": None,
    })
