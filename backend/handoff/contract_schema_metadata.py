from __future__ import annotations

from types import MappingProxyType


# 表示這份封存交接資料使用哪一版格式；
# 如果版本不同，系統不會把它當成目前支援的格式。
PACKAGE_SCHEMA_VERSION = "handoff-package/v1"

# 表示系統用哪一套規則挑選候選文字周邊的上下文（context）；
# context_scope 與 policy binding 必須記錄同一版本。
CONTEXT_POLICY_VERSION = "same-material-local-layout-v1"

# 表示是哪一版驗證程式產生 validation_summary，
# 方便之後查明這次 PASS 或 FAIL 是依哪一版規則判定。
VALIDATOR_VERSION = "handoff-contract-validator/v1"

# 這是禁止直接寫入結果的籠統錯誤名稱；
# 實際失敗必須指出是哪一種 record 的內容指紋（hash）不一致。
RECORD_HASH_MISMATCH = "RECORD_HASH_MISMATCH"

# 這張表是封存交接資料（sealed handoff package）的欄位白名單。
# 每列依序寫明：哪類資料的哪個欄位、應有的格式、是否必填，以及不合格時
# 要留下的錯誤代碼。Validator 會依這張表找出缺少、格式錯誤或未批准的欄位。
_RAW_FIELD_METADATA_ROWS = (
    # Package：下游收到的整包交接結果。它把教材身分、來源綁定、所有 records、
    # PASS／FAIL 狀態與內容指紋放在一起，讓下游不用自行拼湊多份資料。
    ("package", "schema_version", "const handoff-package/v1", True, "PKG_SCHEMA_VERSION_INVALID"),
    ("package", "package_id", "string", True, "PKG_ID_INVALID"),
    ("package", "material_id", "string", True, "PKG_MATERIAL_INVALID"),
    ("package", "status", "enum built|PASS|FAIL", True, "PKG_STATUS_INVALID"),
    ("package", "normalized_source_binding", "artifact_binding", True, "PKG_NORMALIZED_SOURCE_BINDING_INVALID"),
    ("package", "candidate_source_binding", "artifact_binding", True, "PKG_CANDIDATE_BINDING_INVALID"),
    ("package", "context_policy_binding", "policy_binding", True, "PKG_CONTEXT_POLICY_BINDING_INVALID"),
    ("package", "projection_policy_binding", "policy_binding", True, "PKG_PROJECTION_POLICY_BINDING_INVALID"),
    ("package", "candidates", "array<candidate_record>", True, "PKG_CANDIDATES_INVALID"),
    ("package", "origins", "array<origin_record>", True, "PKG_ORIGINS_INVALID"),
    ("package", "contexts", "array<context_record>", True, "PKG_CONTEXTS_INVALID"),
    ("package", "evidence_records", "array<evidence_record>", True, "PKG_EVIDENCE_INVALID"),
    ("package", "projection_records", "array<projection_record>", True, "PKG_PROJECTIONS_INVALID"),
    ("package", "build_attestations", "array<build_attestation_record>", True, "PKG_BUILD_ATTESTATION_INVALID"),
    ("package", "invalid_records", "array<invalid_record>", True, "PKG_INVALID_RECORDS_INVALID"),
    ("package", "content_sha256", "sha256_hex", True, "PKG_CONTENT_HASH_MISMATCH"),
    ("package", "validation_summary", "validation_summary_record", True, "PKG_VALIDATION_SUMMARY_INVALID"),
    ("package", "canonical_sha256", "sha256_hex", True, "PKG_ENVELOPE_HASH_MISMATCH"),

    # Candidate：從 PDF 文字中找出的待處理候選，還不是已確認的最終 Concept。
    # 它必須連到 origin、context 與 evidence，避免下游只憑一段文字做判斷。
    ("candidate", "candidate_id", "string", True, "CANDIDATE_ID_INVALID"),
    ("candidate", "material_id", "string", True, "CANDIDATE_MATERIAL_MISMATCH"),
    ("candidate", "surface", "non_empty_string", True, "CANDIDATE_SURFACE_INVALID"),
    ("candidate", "normalized_surface", "non_empty_string", True, "CANDIDATE_NORMALIZED_SURFACE_INVALID"),
    ("candidate", "extraction_methods", "array<enum>", True, "CANDIDATE_GENERATOR_KINDS_INVALID"),
    ("candidate", "origin_ids", "array<string>", True, "CANDIDATE_ORIGIN_REFS_INVALID"),
    ("candidate", "context_ids", "array<string>", True, "CANDIDATE_CONTEXT_REFS_INVALID"),
    ("candidate", "evidence_ids", "array<string>", True, "CANDIDATE_EVIDENCE_REFS_INVALID"),
    ("candidate", "projection_ids", "array<string>", True, "CANDIDATE_PROJECTION_REFS_INVALID"),
    ("candidate", "support_summary", "object{flags,origin_count,context_count,hard_negative_gate}", True, "CANDIDATE_SUPPORT_INVALID"),
    ("candidate", "build_status", "enum valid|invalid", True, "CANDIDATE_CONSTRUCTION_STATUS_INVALID"),
    ("candidate", "failure_codes", "array<failure_code>", True, "CANDIDATE_FAILURE_CODES_INVALID"),
    ("candidate", "canonical_sha256", "sha256_hex", True, "CANDIDATE_HASH_MISMATCH"),

    # Origin：回答「這個 candidate 原本出現在教材哪裡」。
    # 它保存頁碼、block、layout unit、座標與文字範圍，讓結果能回查 PDF 原位。
    ("origin", "origin_id", "string", True, "ORIGIN_ID_INVALID"),
    ("origin", "candidate_id", "string", True, "ORIGIN_CANDIDATE_REF_INVALID"),
    ("origin", "material_id", "string", True, "ORIGIN_MATERIAL_MISMATCH"),
    ("origin", "block_id", "string", True, "ORIGIN_BLOCK_REF_INVALID"),
    ("origin", "layout_unit_id", "string", True, "ORIGIN_LAYOUT_UNIT_REF_INVALID"),
    ("origin", "source_ref", "string", True, "ORIGIN_SOURCE_REF_INVALID"),
    ("origin", "pdf_page", "integer>=1", False, "ORIGIN_PAGE_INVALID"),
    ("origin", "reading_order", "integer>=0", True, "ORIGIN_READING_ORDER_INVALID"),
    ("origin", "bbox", "array<number>[4]", True, "ORIGIN_BBOX_INVALID"),
    ("origin", "literal_span", "object{start:int,end:int}", True, "ORIGIN_LITERAL_SPAN_INVALID"),
    ("origin", "safe_context_id", "string", True, "ORIGIN_CONTEXT_REF_INVALID"),
    ("origin", "layout_unit_text_sha256", "sha256_hex", True, "ORIGIN_TEXT_HASH_MISMATCH"),
    ("origin", "canonical_sha256", "sha256_hex", True, "ORIGIN_HASH_MISMATCH"),

    # Context：提供 candidate 周圍允許下游閱讀的鄰近文字，幫助理解原句。
    # 它同時記錄取用範圍與邊界，避免誤把跨頁、跨欄或不相干內容接在一起。
    ("context", "context_id", "string", True, "CONTEXT_ID_INVALID"),
    ("context", "material_id", "string", True, "CONTEXT_MATERIAL_MISMATCH"),
    ("context", "text", "non_empty_string", True, "CONTEXT_TEXT_INVALID"),
    ("context", "normalized_text", "non_empty_string", True, "CONTEXT_NORMALIZED_TEXT_INVALID"),
    ("context", "layout_unit_refs", "array<layout_unit_ref>", True, "CONTEXT_LAYOUT_REFS_INVALID"),
    ("context", "primary_candidate_ids", "array<string>", True, "CONTEXT_PRIMARY_CANDIDATES_INVALID"),
    ("context", "context_scope", "const same-material-local-layout-v1", True, "CONTEXT_SCOPE_INVALID"),
    ("context", "start_locator", "layout_locator", True, "CONTEXT_START_LOCATOR_INVALID"),
    ("context", "end_locator", "layout_locator", True, "CONTEXT_END_LOCATOR_INVALID"),
    ("context", "boundary_reason", "object{previous:string,next:string,limits:array<string>}", True, "CONTEXT_BOUNDARY_REASON_INVALID"),
    ("context", "evidence_ids", "array<string>", True, "CONTEXT_EVIDENCE_REFS_INVALID"),
    ("context", "code_point_count", "integer 1..1200", True, "CONTEXT_LENGTH_INVALID"),
    ("context", "canonical_sha256", "sha256_hex", True, "CONTEXT_HASH_MISMATCH"),

    # Evidence：保存實際支持 candidate 的文字、字面範圍與相關 records。
    # 下游可以用它確認候選文字真的存在於來源，而不是只有 builder 的說法。
    ("evidence", "evidence_id", "string", True, "EVIDENCE_ID_INVALID"),
    ("evidence", "material_id", "string", True, "EVIDENCE_MATERIAL_MISMATCH"),
    ("evidence", "evidence_kind", "enum candidate_literal|explicit_alias|heading|definition|projection_literal", True, "EVIDENCE_KIND_INVALID"),
    ("evidence", "statement", "non_empty_string", True, "EVIDENCE_STATEMENT_INVALID"),
    ("evidence", "normalized_statement", "non_empty_string", True, "EVIDENCE_NORMALIZED_STATEMENT_INVALID"),
    ("evidence", "literal_surface", "non_empty_string", True, "EVIDENCE_LITERAL_SURFACE_INVALID"),
    ("evidence", "literal_span", "object{start:int,end:int}", True, "EVIDENCE_LITERAL_SPAN_INVALID"),
    ("evidence", "candidate_ids", "array<string>", True, "EVIDENCE_CANDIDATE_REFS_INVALID"),
    ("evidence", "context_ids", "array<string>", True, "EVIDENCE_CONTEXT_REFS_INVALID"),
    ("evidence", "origin_ids", "array<string>", True, "EVIDENCE_ORIGIN_REFS_INVALID"),
    ("evidence", "canonical_sha256", "sha256_hex", True, "EVIDENCE_HASH_MISMATCH"),

    # Projection：若能從既有文字證據確定地得到另一種字面形式，就記在這裡。
    # 它必須保留來源 records 與演算法版本，不能用來保存沒有來源的語意猜測。
    ("projection", "projection_id", "string", True, "PROJECTION_ID_INVALID"),
    ("projection", "material_id", "string", True, "PROJECTION_MATERIAL_MISMATCH"),
    ("projection", "projection_kind", "enum longer_literal_substring|explicit_alias|heading_definition", True, "PROJECTION_KIND_INVALID"),
    ("projection", "source_candidate_ids", "array<string>", True, "PROJECTION_CANDIDATE_REFS_INVALID"),
    ("projection", "source_context_ids", "array<string>", True, "PROJECTION_CONTEXT_REFS_INVALID"),
    ("projection", "source_evidence_ids", "array<string>", True, "PROJECTION_EVIDENCE_REFS_INVALID"),
    ("projection", "projected_surface", "non_empty_string", True, "PROJECTION_SURFACE_INVALID"),
    ("projection", "normalized_projected_surface", "non_empty_string", True, "PROJECTION_NORMALIZED_SURFACE_INVALID"),
    ("projection", "literal_span", "object{start:int,end:int}", True, "PROJECTION_LITERAL_SPAN_INVALID"),
    ("projection", "algorithm_version", "string", True, "PROJECTION_ALGORITHM_INVALID"),
    ("projection", "canonical_sha256", "sha256_hex", True, "PROJECTION_HASH_MISMATCH"),

    # Build attestation：記錄這包資料由哪個 builder 版本、哪些輸入 artifact 產生，
    # 並保存各類 record 數量，方便重現建置及發現拿錯輸入的情況。
    ("build_attestation", "attestation_id", "string", True, "BUILD_ATTESTATION_ID_INVALID"),
    ("build_attestation", "package_id", "string", True, "BUILD_PACKAGE_REF_INVALID"),
    ("build_attestation", "builder_component", "string", True, "BUILD_COMPONENT_INVALID"),
    ("build_attestation", "builder_version", "string", True, "BUILD_VERSION_INVALID"),
    ("build_attestation", "input_bindings", "array<artifact_binding>", True, "BUILD_INPUT_BINDINGS_INVALID"),
    ("build_attestation", "replay_count", "const 0", True, "BUILD_REPLAY_COUNT_INVALID"),
    ("build_attestation", "replay_content_sha256s", "const []", True, "BUILD_REPLAY_HASH_DRIFT"),
    ("build_attestation", "deterministic_replay_pass", "const false", True, "BUILD_REPLAY_FAILED"),
    ("build_attestation", "record_counts", "object", True, "BUILD_RECORD_COUNTS_MISMATCH"),
    ("build_attestation", "canonical_sha256", "sha256_hex", True, "BUILD_ATTESTATION_HASH_MISMATCH"),

    # Invalid record：保存哪筆資料沒有通過驗收，以及具體原因與錯誤代碼。
    # 只要這裡有任何紀錄，整包就是 FAIL，避免錯誤被刪除或靜默忽略。
    ("invalid_record", "invalid_record_id", "string", True, "INVALID_RECORD_ID_INVALID"),
    ("invalid_record", "collection", "enum candidates|origins|contexts|evidence_records|projection_records|build_attestations|package", True, "INVALID_RECORD_COLLECTION_INVALID"),
    ("invalid_record", "record_id", "string", True, "INVALID_RECORD_TARGET_INVALID"),
    ("invalid_record", "failure_codes", "array<failure_code>", True, "INVALID_RECORD_CODES_INVALID"),
    ("invalid_record", "reason", "non_empty_string", True, "INVALID_RECORD_REASON_INVALID"),
    ("invalid_record", "canonical_sha256", "sha256_hex", True, "INVALID_RECORD_HASH_MISMATCH"),

    # Validation summary：讓下游快速看到本次驗收由哪版 validator 執行、
    # 最終是 PASS 或 FAIL，以及各類錯誤有幾筆；詳細原因仍保存在 invalid records。
    ("validation_summary", "validation_run_id", "string", True, "VALIDATION_RUN_ID_INVALID"),
    ("validation_summary", "validator_version", "string", True, "VALIDATOR_VERSION_INVALID"),
    ("validation_summary", "validated_content_sha256", "sha256_hex", True, "VALIDATED_CONTENT_HASH_MISMATCH"),
    ("validation_summary", "status", "enum PASS|FAIL", True, "VALIDATION_STATUS_INVALID"),
    ("validation_summary", "failure_count", "integer>=0", True, "VALIDATION_FAILURE_COUNT_MISMATCH"),
    ("validation_summary", "failure_code_counts", "object<string,integer>=0>", True, "VALIDATION_FAILURE_AGGREGATE_MISMATCH"),
)

# 說明正式 package 為何不能把 replay 結果寫進自己。
# 真正的 replay 次數、hash 與結果會在 package 外比較，這裡只保存這項限制。
_ACTIVE_METADATA_INVARIANTS = MappingProxyType(
    {
        ("build_attestation", "replay_count"): (
            "Production sealed packages require 0; isolated replay counts remain "
            "package-external."
        ),
        ("build_attestation", "replay_content_sha256s"): (
            "Production sealed packages require []; replay hashes remain "
            "package-external and cannot create a runtime hash self-reference."
        ),
        ("build_attestation", "deterministic_replay_pass"): (
            "Production sealed packages require false; replay pass/fail is "
            "determined only by package-external comparison."
        ),
    }
)

# 把上面的每列資料換成有欄位名稱的格式，讓程式和測試能直接讀出
# 資料種類、欄位名稱、格式、是否必填與錯誤代碼。
FIELD_METADATA_ROWS = tuple(
    MappingProxyType(
        {
            "collection": collection,
            "path": path,
            "type": field_type,
            "required": required,
            "validation_failure_code": failure_code,
            **(
                {"invariant": _ACTIVE_METADATA_INVARIANTS[(collection, path)]}
                if (collection, path) in _ACTIVE_METADATA_INVARIANTS
                else {}
            ),
        }
    )
    for collection, path, field_type, required, failure_code
    in _RAW_FIELD_METADATA_ROWS
)

# 再依 package、candidate、origin 等資料種類分組，
# 讓 validator 不必每次掃過全部 101 列就能找到指定欄位的規則。
FIELD_METADATA = MappingProxyType(
    {
        collection: MappingProxyType(
            {
                row["path"]: row
                for row in FIELD_METADATA_ROWS
                if row["collection"] == collection
            }
        )
        for collection in {
            row["collection"]
            for row in FIELD_METADATA_ROWS
        }
    }
)

# 告訴程式每種單筆資料放在 package 的哪個清單中。
# 例如 candidate 放在 candidates，origin 放在 origins。
COLLECTION_KEYS = MappingProxyType(
    {
        "candidate": "candidates",
        "origin": "origins",
        "context": "contexts",
        "evidence": "evidence_records",
        "projection": "projection_records",
        "build_attestation": "build_attestations",
        "invalid_record": "invalid_records",
    }
)

# 告訴程式每種資料要用哪個欄位當作自己的 ID，
# 方便建立索引、檢查重複資料，以及指出是哪一筆資料出錯。
COLLECTION_ID_FIELDS = MappingProxyType(
    {
        "candidate": "candidate_id",
        "origin": "origin_id",
        "context": "context_id",
        "evidence": "evidence_id",
        "projection": "projection_id",
        "build_attestation": "attestation_id",
        "invalid_record": "invalid_record_id",
    }
)

# 當資料多出契約沒有允許的欄位時，依資料種類選擇明確的錯誤代碼，
# 並讓整包資料維持 FAIL，而不是忽略陌生欄位。
UNKNOWN_FIELD_CODES = MappingProxyType(
    {
        "package": "PKG_FIELD_INVALID",
        "candidate": "CANDIDATE_FIELD_INVALID",
        "origin": "ORIGIN_FIELD_INVALID",
        "context": "CONTEXT_FIELD_INVALID",
        "evidence": "EVIDENCE_FIELD_INVALID",
        "projection": "PROJECTION_FIELD_INVALID",
        "build_attestation": "BUILD_ATTESTATION_FIELD_INVALID",
        "invalid_record": "INVALID_RECORD_FIELD_INVALID",
        "validation_summary": "VALIDATION_SUMMARY_FIELD_INVALID",
    }
)

# 禁止把測試答案、評分結果或教材特製規則放進正式 package。
# 即使這些欄位藏在更深層的資料中，validator 仍會找到並拒絕。
RUNTIME_FORBIDDEN_FIELDS = frozenset(
    {
        "gold_slot_id",
        "gold_name",
        "gold_aliases",
        "coverage",
        "coverage_rate",
        "miss_name",
        "miss_reason",
        "quality_label",
        "evaluation_label",
        "material_specific_mapping",
        "expected_concept",
    }
)

# 這些錯誤代碼只用來辨識與攔截，不能出現在正式結果。
# `RECORD_HASH_MISMATCH` 太籠統，正式錯誤必須指出是哪種資料的 hash 不一致。
RESERVED_NON_EMITTED_CODES = frozenset({RECORD_HASH_MISMATCH})
