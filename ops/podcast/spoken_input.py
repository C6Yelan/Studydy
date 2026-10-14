"""只產生 TTS 輸入；原講稿不改。依字詞類型處理，不設術語替換表。"""
import re
from decimal import Decimal
from functools import lru_cache
import cmudict
import wordninja

_PREFIX = {'': '', 'k': 'kilo', 'K': 'kilo', 'M': 'mega', 'G': 'giga', 'T': 'tera', 'P': 'peta', 'E': 'exa'}
# 資訊量／速率的大小寫是語意：僅在數值旁辨識單位，不把一般字母 B 當 byte。
_UNIT = r'(?<![A-Za-z0-9_])(?P<number>[+-]?\d+(?:\.\d+)?)\s*(?P<prefix>[kKMGTPE]?)(?P<base>[Bb])(?P<rate>/s|ps)?(?![A-Za-z0-9_])'
_LATIN = r'[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*'
_PARTS = re.compile(f'(?P<unit>{_UNIT})|(?P<latin>{_LATIN})')
_CAMEL = re.compile(r'[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+|[A-Z]')

@lru_cache(maxsize=1)
def lexicon():
    return cmudict.dict()


def letter_phones(letter):
    choices = lexicon().get(letter.lower())
    if not choices:
        raise ValueError('UNSUPPORTED_LETTER')
    # 單獨字母使用有重音的字母名稱，避免 A 被讀成冠詞的弱讀音。
    return max(choices, key=lambda p: sum('1' in x for x in p))


def _same_sounds(a, b):
    return [re.sub(r'\d', '', p) for p in a] == [re.sub(r'\d', '', p) for p in b]


def initialism(token):
    if (token.isalpha() and token.isupper()) or (len(token) == 1 and token.isalpha()):
        return True
    if not token.isalpha():
        return False
    spelled = [p for c in token for p in letter_phones(c)]
    return any(_same_sounds(p, spelled) for p in lexicon().get(token.lower(), []))


def english_words(token, normalize_number):
    if token.isdigit():
        return normalize_number(token)
    if initialism(token):
        return ' '.join(token.upper())
    if '_' in token or '-' in token or (not token.islower() and not token.isupper()) or any(c.isdigit() for c in token):
        chunks=[]
        for part in re.split(r'([_-])',token):
            if part in {'_', '-'}:
                chunks.append('underscore' if part == '_' else 'hyphen')
            else:
                pieces = _CAMEL.findall(part)
                if pieces == [token]:
                    return english_words(token.lower(), normalize_number)
                chunks.extend(english_words(p, normalize_number) for p in pieces)
        return ' '.join(chunks)
    if token.lower() in lexicon():
        return token.lower()
    pieces = wordninja.split(token.lower())
    if len(pieces)>1 and all(p in lexicon() for p in pieces):
        return ' '.join(pieces)
    return ' '.join(token.upper())


def mixed_sentences(text, normalize_zh, normalize_en):
    # 千分位是同一數值；毫秒只在數值後展開，不碰識別符中的 ms。
    text = re.sub(r'(?<![A-Za-z0-9_.])\d{1,3}(?:,\d{3})+(?:\.\d+)?', lambda m: m[0].replace(',', ''), text)
    text = re.sub(r'(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)\s*ms\b', r'\1 毫秒', text)
    result = []

    for sentence in re.split(r'(?<=[。！？])|(?<=[.!?])\s+', text):
        if not sentence.strip():
            continue
        parts = []
        cursor = 0
        for match in _PARTS.finditer(sentence):
            prefix = sentence[cursor:match.start()]
            parts.append(normalize_zh(prefix) if prefix.strip() else prefix)
            if match.group('unit'):
                base = 'byte' if match.group('base') == 'B' else 'bit'
                if abs(Decimal(match.group('number'))) != 1:
                    base += 's'
                value = normalize_en(match.group('number')) + ' ' + _PREFIX[match.group('prefix')] + base
                if match.group('rate'):
                    value += ' per second'
            else:
                token = match.group('latin')
                # 小寫 a 是冠詞；全大寫縮寫才逐字母讀，保留同句中文連接詞。
                value = token.lower() if not token.isupper() and token.lower() in lexicon() else english_words(token, normalize_en)
            parts.append(' ' + value + ' ')
            cursor = match.end()
        suffix = sentence[cursor:]
        parts.append(normalize_zh(suffix) if suffix.strip() else suffix)
        value = re.sub(r'\s+', ' ', ''.join(parts)).strip()
        result.append({'language': 'mixed', 'text': value, 'ending': sentence.rstrip()[-1]})
    return result
