"""翻譯可改寫散文；數量、單位、程式片段與公式仍須逐項對回來源。"""

from __future__ import annotations

import re
import unicodedata


LITERAL_PROJECTION = "localized_meaning_v2"
EXCERPT_PROJECTION = "spoken_excerpt_meaning/v1"
EXCERPT_REPAIR = "spoken_excerpt_literal_repair/v1"
EXCERPT_PROJECTIONS = {EXCERPT_PROJECTION, EXCERPT_REPAIR}
LITERAL_REPAIRS = {"source_literal_repair", EXCERPT_REPAIR}

_NUMBER = r"[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+−-]?\d+)?"
_UNIT = (
    r"(?:[%％]|°[CF]|(?:[fpnumkKMGTµμ]?(?:mol|Hz|Pa|Wb|[smgAKNJWCVFΩSTHBb])"
    r"|bits?|bytes?)(?:\^?[²³23])?(?:/(?:[munµμ]?s|[km]?m|h)(?:\^?[²³23])?)?)"
)
_QUANTITY = re.compile(rf"(?<![A-Za-z0-9_.]){_NUMBER}(?:\s*{_UNIT}(?![A-Za-z]))?")
_QUOTED = re.compile(
    # PDF 可在行內程式碼中換行；不能把其結尾反引號誤當下一段的開頭。
    # 仍不跨空白段落配對，避免把一般說明一起保護成程式碼。
    r"(?<!`)`(?:(?!\n[ \t\r]*\n)[^`])+`(?!`)|(?<![A-Za-z0-9])\'(?:\\.|[^\'\\\n]){1,80}\'"
    r'|(?<![A-Za-z0-9])"(?:\\.|[^"\\\n]){1,80}"'
    r'|\\(?:[0abfnrtv\\\'"?]|x[0-9A-Fa-f]+|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})'
)
_CODE_FENCE = re.compile(r"(?<!`)(`{3,})([\s\S]*?)\1(?!`)")
_CALL = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?:\([^()\n]{0,80}\)|\[[^\]\n]{1,80}\])"
)
# 保留物件欄位存取，避免把 P.tools then return 的尾端誤認為英文散文。
_MEMBER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_GLOSS = re.compile(r"[（(]([A-Za-z][A-Za-z0-9_-]*|[α-ωΑ-Ω])[）)]")
_OPERAND = r"(?:[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*|\d+(?:\.\d+)?)"
_EXPRESSION = re.compile(
    rf"(?<![A-Za-z0-9_*])(?:[A-Za-zα-ωΑ-Ω](?:_[A-Za-z0-9]+)?|\d+(?:\.\d+)?)"
    rf"(?:\s*(?:==|!=|<=|>=|->|=|\+|\*|/|\^|<|>|≤|≥|≠)\s*{_OPERAND})+(?![A-Za-z0-9_α-ωΑ-Ω*])"
)
# 翻譯流程圖時保護多字母節點；不將這些節點加入已發布內容的字面值契約，
# 也不把 difference +0.1 等一般敘述或 HTML 標籤辨識為運算式。
_FLOW_EXPRESSION = re.compile(
    rf"(?<![A-Za-z0-9_*]){_OPERAND}"
    rf"(?:[ \t]*(?:=|\+|\*|/|\^|≤|≥|≠|<=|>=)[ \t]*{_OPERAND})*"
    rf"(?:[ \t]*->[ \t]*{_OPERAND})+"
    rf"(?:[ \t]*(?:=|\+|\*|/|\^|≤|≥|≠|<=|>=)[ \t]*{_OPERAND})*"
    rf"(?![A-Za-z0-9_α-ωΑ-Ω*])"
)
_SUBTRACTION = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-zα-ωΑ-Ω]|\d+(?:\.\d+)?)\s*[-−]\s*(?:[A-Za-zα-ωΑ-Ω]|\d+(?:\.\d+)?)(?![A-Za-z0-9_])"
)
_DIRECTION = re.compile(r"[↑↓←→↔⇐⇒⇔≤≥≠±∓]")
_DECLARATION = re.compile(
    r"\b(?:(?:const|static|unsigned|signed)\s+)*(?:char|int|float|double|bool|void)\s+[*&\w\s]+;"
)
_LATEX = re.compile(r"\$\$[\s\S]+?\$\$|\$[^$\n]+\$")
_MONTHS = (
    "January February March April May June July August September October November December"
).split()
_NAMED_DATE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(\d{4})\b", re.I
)


def _formula_text(text):
    if not re.match(
        r"^\s*[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*(?:\([^()\n]*\))?\s*(?:=|∼|≈|∈)", text
    ):
        return False
    without_functions = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\s*\()", "", text)
    return re.search(r"[\u3400-\u9fff]|\b[a-z]{4,}\b", without_functions) is None


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _literals(text: str) -> set[str]:
    # 英文連字號與一般括注不代表程式碼；f(x)、陣列及數學運算才保護整個片段。
    result = {
        _compact(match[0].strip("`"))
        for pattern in (
            _QUANTITY,
            _QUOTED,
            _CALL,
            _EXPRESSION,
            _SUBTRACTION,
            _DIRECTION,
        )
        for match in pattern.finditer(text)
    }
    result.update(
        match[0] if len(match[1]) == 1 else match[1] for match in _GLOSS.finditer(text)
    )
    return result


def _literal_ranges(text: str, kind: str):
    spans = [
        (match.start(), match.end())
        for pattern in (
            _QUANTITY,
            _QUOTED,
            _CODE_FENCE,
            _CALL,
            _MEMBER,
            _EXPRESSION,
            _FLOW_EXPRESSION,
            _SUBTRACTION,
            _DIRECTION,
            _GLOSS,
            _DECLARATION,
            _LATEX,
        )
        for match in pattern.finditer(text)
    ]
    if kind in {"code", "formula"} or _formula_text(text):
        spans = [(0, len(text))]
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def mask_literals(
    text: str, kind: str = "paragraph", *, token_start=0
) -> tuple[str, dict[str, str]]:
    """翻譯時以位置代號保留技術片段；重疊的公式／數字只保留一次。"""
    merged = _literal_ranges(text, kind)
    prefix = "⟪保留"
    while prefix in text:
        prefix += "值"
    pieces, literals, cursor = [], {}, 0
    for number, (start, end) in enumerate(merged, start=token_start):
        token = f"{prefix}{number}⟫"
        pieces.extend((text[cursor:start], token))
        literals[token] = text[start:end]
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), literals


def language_correction_template(text: str):
    """中文只混入外語詞時，保護其餘整段，避免修字時擅自重排原公式。"""
    if not re.search(r"[\u3400-\u9fff]", text):
        return mask_literals(text)
    protected = _literal_ranges(text, "paragraph")
    foreign = [
        match.span()
        for match in re.finditer(r"[\u0400-\u1cff]+(?:\s+[\u0400-\u1cff]+)*", text)
        if not any(
            start <= match.start() and match.end() <= end for start, end in protected
        )
    ]
    if not foreign:
        return mask_literals(text)
    prefix = "⟪保留"
    while prefix in text:
        prefix += "值"
    parts, literals, cursor = [], {}, 0
    for start, end in [*foreign, (len(text), len(text))]:
        if start > cursor:
            token = f"{prefix}{len(literals)}⟫"
            literals[token] = text[cursor:start]
            parts.append(token)
        parts.append(text[start:end])
        cursor = end
    return "".join(parts), literals


def restore_literals(value, literals):
    if not isinstance(value, str) or any(value.count(token) != 1 for token in literals):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    if any(token not in literals for token in re.findall(r"⟪保留值*\d+⟫", value)):
        raise ValueError("SEMANTIC_OUTPUT_INVALID")
    if not literals:
        return value
    # 一次還原，不把原始程式字串中的代號再次當成另一個替換目標。
    return re.sub(
        "|".join(re.escape(token) for token in literals),
        lambda match: literals[match[0]],
        value,
    )


def literal_differences(
    meaning: str, sources: list[dict], *, legacy: bool = False
) -> tuple[set[str], set[str]]:
    """驗證與模型補正共用缺漏／新增字面值，不讓診斷再次漏掉短數值。"""
    source_text = " ".join(source["exact_text"] for source in sources)
    # 不把前一區塊末尾與下一區塊開頭誤接成另一個公式或單位。
    source_literals = set().union(
        *(_literals(source["exact_text"]) for source in sources)
    )
    meaning_literals = _literals(meaning)
    compact_source, compact_meaning = _compact(source_text), _compact(meaning)
    # 英文日期的月份可譯成中文數字月份；仍須保留同一年及原數值，不能憑月份放行任意新數字。
    calendar_numbers = set()
    for date in _NAMED_DATE.finditer(source_text):
        month = next(
            i
            for i, name in enumerate(_MONTHS, 1)
            if name.casefold() == date[1].casefold()
        )
        if re.search(re.escape(date[2]) + r"\s*年\s*" + str(month) + r"\s*月", meaning):
            calendar_numbers.add(str(month))
    # 原文括注可移到句中；保留既有非數量字面值規則。
    missing = {literal for literal in source_literals if literal not in compact_meaning}
    unexpected = {
        literal for literal in meaning_literals
        if literal not in compact_source and literal not in calendar_numbers
    }
    if not legacy:
        # 數量以完整 token 比較；獨立的 1 不能由 10 或 -1 代替。
        # 不比次數，允許同一事實去重；逐個來源取值，避免跨區塊拼成單位。
        source_quantities = {
            _compact(match[0])
            for source in sources
            for match in _QUANTITY.finditer(source["exact_text"])
        }
        meaning_quantities = {_compact(match[0]) for match in _QUANTITY.finditer(meaning)}
        missing.update(source_quantities - meaning_quantities)
        unexpected.update(meaning_quantities - source_quantities - calendar_numbers)
    return missing, unexpected


def preserves_literals(meaning: str, sources: list[dict], *, legacy: bool = False) -> bool:
    """共用於生成投影、審查修正及發布驗證，不以英文全文相等代替忠實度檢查。"""
    source_text = " ".join(source["exact_text"] for source in sources)
    normalized = " ".join(meaning.split())
    if normalized == " ".join(source_text.split()):
        return True
    if any(literal_differences(meaning, sources, legacy=legacy)):
        return False
    for source in sources:
        text = source["exact_text"]
        if (
            source.get("kind") in {"code", "formula"}
            and " ".join(text.split()) not in normalized
        ):
            return False
        # 原生文字有時把 C 類宣告標成 paragraph；不能因翻譯而丟掉宣告內容。
        for declaration in _DECLARATION.finditer(text):
            if " ".join(declaration[0].split()) not in normalized:
                return False
    return True


_CLAUSE_PUNCTUATION = frozenset("。！？；，：.!?;,:")
MIN_EXCERPT_CHARACTERS = 4


def _is_boundary_punctuation(text, index):
    """半形標點須後接空白或原文結尾才算邊界，避免從 10:30、1:2、v1.2.3 中間切開；全形標點不受限。"""
    char = text[index]
    if char not in _CLAUSE_PUNCTUATION:
        return False
    return not char.isascii() or index + 1 == len(text) or text[index + 1].isspace()


def _clause_bounds(text, start, end):
    """只依來源的句／子句標點切分；無標點時保守引用整段，空白不單獨建立邊界。"""
    before = text[:start].rstrip()
    after_index = len(text) - len(text[end:].lstrip())
    quote_end = start + len(text[start:end].rstrip())
    return (not before or _is_boundary_punctuation(text, len(before) - 1)) and (
        after_index == len(text)
        or _is_boundary_punctuation(text, after_index)
        or (quote_end > start and _is_boundary_punctuation(text, quote_end - 1))
    )


def transcript_evidence_ids(document):
    """從已綁定來源取得逐字稿 Evidence；不以 Claim／概念名稱猜來源。"""
    binding = document["input_binding"]
    sources = {item["source_id"] for item in binding["manifest"]["items"] if "transcript" in item}
    pages = binding["bundle"]["pages"]
    return {
        item["evidence_id"] for item in document["evidence"]
        if pages[item["page"] - 1]["source_id"] in sources
    }


def quoted_sources(spans, evidence, *, excerpts=False, excerpt_evidence_ids=None):
    """節錄須足夠長、唯一逐字定位且位於子句邊界，不能切斷技術字面值。"""
    sources = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"evidence_id", "quote"}:
            return None
        source = evidence.get(span["evidence_id"])
        quote = span["quote"]
        if source is None or not isinstance(quote, str) or not quote.strip():
            return None
        spoken = excerpts and (
            excerpt_evidence_ids is None or span["evidence_id"] in excerpt_evidence_ids
        )
        if spoken and sum(
            not char.isspace() and not unicodedata.category(char).startswith("P")
            for char in quote
        ) < MIN_EXCERPT_CHARACTERS:
            return None
        text = source["exact_text"]
        if quote != text:
            if not spoken:
                return None
            start = text.find(quote)
            end = start + len(quote)
            if start < 0 or text.find(quote, start + 1) >= 0:
                return None
            if not _clause_bounds(text, start, end):
                return None
            if any(
                a < start < b or a < end < b
                for a, b in _literal_ranges(text, source.get("kind", "paragraph"))
            ):
                return None
        sources.append({**source, "exact_text": quote})
    return sources
