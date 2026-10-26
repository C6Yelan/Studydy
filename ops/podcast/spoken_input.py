"""只產生 TTS 輸入；原講稿不改。依字詞類型、明確公式與縮寫念法處理。"""

import re
from decimal import Decimal
from functools import lru_cache
import cmudict
import wordninja

_PRONUNCIATIONS = {"RAG": "R A G", "DDPM": "D D P M"}

_PREFIX = {
    "": "",
    "k": "kilo",
    "K": "kilo",
    "M": "mega",
    "G": "giga",
    "T": "tera",
    "P": "peta",
    "E": "exa",
}
# 資訊量／速率的大小寫是語意：僅在數值旁辨識單位，不把一般字母 B 當 byte。
_UNIT = r"(?<![A-Za-z0-9_])(?P<number>[+-]?\d+(?:\.\d+)?)\s*(?P<prefix>[kKMGTPE]?)(?P<base>[Bb])(?P<rate>/s|ps)?(?![A-Za-z0-9_])"
_LATIN = r"[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*"
_PARTS = re.compile(f"(?P<unit>{_UNIT})|(?P<latin>{_LATIN})")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+|[A-Z]")
_GREEK = dict(
    zip(
        "αβγδεθλμρστφω",
        [
            "alpha",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "theta",
            "lambda",
            "mu",
            "rho",
            "sigma",
            "tau",
            "phi",
            "omega",
        ],
    )
)


@lru_cache(maxsize=1)
def lexicon():
    return cmudict.dict()


def letter_phones(letter):
    choices = lexicon().get(letter.lower())
    if not choices:
        raise ValueError("UNSUPPORTED_LETTER")
    # 單獨字母使用有重音的字母名稱，避免 A 被讀成冠詞的弱讀音。
    return max(choices, key=lambda p: sum("1" in x for x in p))


def _same_sounds(a, b):
    return [re.sub(r"\d", "", p) for p in a] == [re.sub(r"\d", "", p) for p in b]


def initialism(token):
    if (token.isalpha() and token.isupper()) or (len(token) == 1 and token.isalpha()):
        return True
    if not token.isalpha():
        return False
    spelled = [p for c in token for p in letter_phones(c)]
    return any(_same_sounds(p, spelled) for p in lexicon().get(token.lower(), []))


def english_words(token, normalize_number):
    if token in _PRONUNCIATIONS:
        return _PRONUNCIATIONS[token]
    if token.isdigit():
        return normalize_number(token)
    if initialism(token):
        return " ".join(token.upper())
    # 一般英文複合詞的連字號不朗讀；代碼、縮寫及數值中的符號仍保留。
    words = token.split("-")
    if len(words) > 1 and all(
        word.isalpha() and not initialism(word) and word.lower() in lexicon()
        for word in words
    ):
        return " ".join(word.lower() for word in words)
    if (
        "_" in token
        or "-" in token
        or (not token.islower() and not token.isupper())
        or any(c.isdigit() for c in token)
    ):
        chunks = []
        for part in re.split(r"([_-])", token):
            if part in {"_", "-"}:
                chunks.append("underscore" if part == "_" else "hyphen")
            else:
                pieces = _CAMEL.findall(part)
                if pieces == [token]:
                    return english_words(token.lower(), normalize_number)
                chunks.extend(english_words(p, normalize_number) for p in pieces)
        return " ".join(chunks)
    if token.lower() in lexicon():
        return token.lower()
    pieces = wordninja.split(token.lower())
    if len(pieces) > 1 and all(p in lexicon() for p in pieces):
        return " ".join(pieces)
    return " ".join(token.upper())


def spoken_phrases(text):
    """只在可辨識的句內列舉留停頓，括號公式、小數與路徑不拆開。"""
    pattern = r'.+?(?:[。！？]+[」』”\'"）)\]]*|[.!?]+[」』”\'"）)\]]*(?=\s|$)|$)'
    for match in re.finditer(pattern, text, re.S):
        sentence = match.group().strip()
        # 全形斜線的三項以上中文詞串可辨識為列舉；兩項比值、ASCII 路徑與單位不套用。
        list_boundaries = set()
        for listing in re.finditer(
            r"(?:[\u3400-\u9fff]+|<[\u3400-\u9fff]+>)(?:／(?:[\u3400-\u9fff]+|<[\u3400-\u9fff]+>)){2,}",
            sentence,
        ):
            list_boundaries.update(
                i for i in range(listing.start(), listing.end()) if sentence[i] == "／"
            )
        start = 0
        stack = []
        code = False
        for i, char in enumerate(sentence):
            if char == "`":
                code = not code
            if code:
                continue
            if char in "([{（":
                stack.append(char)
            elif char in ")]}）" and stack:
                stack.pop()
            if stack:
                continue
            separator = (
                char in "；;、"
                or char in "，,"
                and i - start >= 24
                or i in list_boundaries
            )
            if (
                char == ","
                and 0 < i < len(sentence) - 1
                and sentence[i - 1].isdigit()
                and sentence[i + 1].isdigit()
            ):
                separator = False
            if separator and sentence[start : i + 1].strip():
                yield sentence[start : i + 1].strip(), char
                start = i + 1
        if sentence[start:].strip():
            yield sentence[start:].strip(), None


def mixed_sentences(text, normalize_zh, normalize_en):
    result = []
    # 句尾引號留在同一句，避免只剩引號的片段被送去合成；小數點不當句界。
    for source_text, separator in spoken_phrases(text):
        if not source_text:
            continue
        # 判斷句長使用原文字；縮寫、數值的朗讀展開不改變停頓類型。
        sentence = re.sub(
            r"(?<![A-Za-z0-9_.])\d{1,3}(?:,\d{3})+(?:\.\d+)?",
            lambda m: m[0].replace(",", ""),
            source_text,
        )
        sentence = re.sub(
            r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)\s*ms\b", r"\1 毫秒", sentence
        )
        sentence = re.sub(r"<([\u3400-\u9fff]+)>", r"\1標記", sentence)
        if separator == "／":
            sentence = sentence[:-1] + "，"
        # 只展開可辨識的運算符；程式字面值與路徑保留原符號，不能變成數學敘述。
        pieces = re.split(
            r"(`[^`]*`|(?:https?://|(?<!\w)/|(?<!\w)[A-Za-z]:\\)[^\s，。；、]+)",
            sentence,
        )
        for i in range(0, len(pieces), 2):
            # 只有明示二選一的詞串讀「或」；協定、比值及路徑不從斜線猜語意。
            pieces[i] = re.sub(
                r"((?:二選一)[：:]?\s*)([A-Za-z\u3400-\u9fff]+)[/／]([A-Za-z\u3400-\u9fff]+)",
                r"\1\2 或 \3", pieces[i],
            )
            pieces[i] = re.sub(r"(?<![A-Za-z0-9_/])(\d+)/(\d+)(?![A-Za-z0-9_/])", r"\2 分之 \1", pieces[i])
            pieces[i] = re.sub(r"(?<![A-Za-z0-9_])QK\^T\s*/\s*√d_k(?![A-Za-z0-9_])", "Q 乘以 K 的轉置，除以根號 d k", pieces[i])
            pieces[i] = re.sub(r"(?<![A-Za-z0-9_])QK\^T(?![A-Za-z0-9_])", "Q 乘以 K 的轉置", pieces[i])
            pieces[i] = re.sub(r"√d_k(?![A-Za-z0-9_])", "根號 d k", pieces[i])
            pieces[i] = pieces[i].replace("→", " 到 ").replace("←", " 左箭頭 ")
            pieces[i] = re.sub(r"(?<=[\w)])\s*\+\s*(?=[\w(])", " 加 ", pieces[i])
            pieces[i] = re.sub(r"(?<![<>=!])=(?!=)", " 等於 ", pieces[i])
            pieces[i] = re.sub(r"[、；;：（）()]", "，", pieces[i])
            pieces[i] = re.sub(r"(?<!\d):(?!\d)", "，", pieces[i])
            pieces[i] = re.sub(
                r"(?<=[\w)])\s*(<=|>=|≤|≥|<|>)\s*(?=[\w(])",
                lambda match: " "
                + {
                    "<=": "小於或等於",
                    ">=": "大於或等於",
                    "≤": "小於或等於",
                    "≥": "大於或等於",
                    "<": "小於",
                    ">": "大於",
                }[match[1]]
                + " ",
                pieces[i],
            )
            pieces[i] = re.sub(
                r"[αβγδεθλμρστφω]",
                lambda match: " " + _GREEK[match[0]] + " ",
                pieces[i],
            )
        sentence = "".join(pieces)
        parts = []
        cursor = 0
        for match in _PARTS.finditer(sentence):
            prefix = sentence[cursor : match.start()]
            parts.append(normalize_zh(prefix) if prefix.strip() else prefix)
            if match.group("unit"):
                base = "byte" if match.group("base") == "B" else "bit"
                if abs(Decimal(match.group("number"))) != 1:
                    base += "s"
                value = (
                    normalize_en(match.group("number"))
                    + " "
                    + _PREFIX[match.group("prefix")]
                    + base
                )
                if match.group("rate"):
                    value += " per second"
            else:
                token = match.group("latin")
                # 小寫 a 是冠詞；全大寫縮寫才逐字母讀，保留同句中文連接詞。
                value = (
                    token.lower()
                    if not token.isupper()
                    and (token.lower() in lexicon() or token in _GREEK.values())
                    else english_words(token, normalize_en)
                )
            parts.append(" " + value + " ")
            cursor = match.end()
        suffix = sentence[cursor:]
        parts.append(normalize_zh(suffix) if suffix.strip() else suffix)
        value = re.sub(r"\s+", " ", "".join(parts)).strip()
        result.append(
            {
                "language": "mixed",
                "text": value,
                "source_text": source_text,
                "ending": separator
                if separator is not None
                else source_text.rstrip("」』”'\"）)]")[-1],
            }
        )
    return result
