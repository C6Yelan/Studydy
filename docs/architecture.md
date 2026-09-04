# Studydy final architecture

Production has one semantic path:

```text
PDF → native Evidence / optional OCR → document sections + Evidence bundle
    → resident Qwen3.8 unified semantics → deterministic projection
    → Document Tree + canonical Concepts + typed Relations + Initial Path
    → StudySession + Assessment + learner guidance
```

Qwen owns Concept boundaries, Claim meaning, cross-section consolidation, Relation proposals/reasons,
and Assessment semantics. Code owns source identity, Evidence/span binding, exact technical literals,
schema, ownership, endpoints, duplicates/conflicts, prerequisite cycles, private answers, scoring,
and stale/idempotency/concurrency behavior.

The only Relation types are `prerequisite`, `part_of`, `application`, `example`, and `contrast`.
`prerequisite` is the only Relation that can change Initial Path order or create a learner prerequisite
gap. Document Tree placement always comes from document structure.

There is one production Python minor (3.12), one externally resident
`Qwen/Qwen3.8-27B-FP8` service, and one optional Unlimited-OCR child. The backend never starts,
stops, swaps, or unloads Qwen. Assessment uses the same authenticated loopback service.
mDeBERTa is removed.

Pre-release persistence is a clean final schema. `knowledge_structures` stores one immutable artifact
instead of parallel material/map artifacts. No legacy reader, writer, adapter, or upgrade path exists.
