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

Material semantics uses the compact v2 wire contract. Each Evidence row contains only a material-local
integer handle, page, kind, and exact text, grouped once under its section title. Claims select
`[handle, start, end]` Unicode character ranges (`[handle, 0, 0]` selects the whole block); a null meaning
reuses the selected source text. Code restores canonical IDs, quotes, section references, and the
Relation basis. These temporary handles are never persisted as canonical identities.

Bundles are packed using the resident tokenizer with the actual prompt and current Concept catalog,
reserving 4096 output tokens within the unchanged 32768-token context. A truncated response fails;
it does not count as a successful material or trigger additional split calls.
Material generation explicitly pins the existing thinking/xhigh template and sampling settings in
the runtime lock; packing and inference use the same template options. Relation instructions retain
supported edges while distinguishing necessary dependencies, concrete uses, and the entities being
compared. Assessment settings are unchanged.

The only Relation types are `prerequisite`, `part_of`, `application`, `example`, and `contrast`.
`prerequisite` is the only Relation that can change Initial Path order or create a learner prerequisite
gap. Document Tree placement always comes from document structure.

There is one production Python minor (3.12), one externally resident
`Qwen/Qwen3.8-27B-FP8` service, and one optional Unlimited-OCR child. The backend never starts,
stops, swaps, or unloads Qwen. Assessment uses the same authenticated loopback service.
mDeBERTa is removed.

Pre-release persistence is a clean final schema. `knowledge_structures` stores one immutable artifact
instead of parallel material/map artifacts. No legacy reader, writer, adapter, or upgrade path exists.
