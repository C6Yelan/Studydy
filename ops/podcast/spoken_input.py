"""只產生 TTS 輸入；原講稿不改。依字詞類型處理，不設術語替換表。"""
import re
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


def speech_spans(text, normalize_zh, normalize_en):
    """按語言與記號類型建立可讀語音片段；不讓英文片段再次通過中文去空白規則。"""
    nodes=[];pending='';cursor=0
    def flush():
        nonlocal pending
        if pending.strip():nodes.append({'language':'zh','text':normalize_zh(pending)})
        pending=''
    for match in _PARTS.finditer(text):
        pending+=text[cursor:match.start()]
        if match.group('unit'):
            from decimal import Decimal
            # 保留原本數值，以完整英文單位區分 byte／bit，不展開成中文大數量。
            flush()
            base = 'byte' if match.group('base') == 'B' else 'bit'
            if abs(Decimal(match.group('number'))) != 1:
                base += 's'
            value = normalize_en(match.group('number')) + ' ' + _PREFIX[match.group('prefix')] + base
            if match.group('rate'):
                value += ' per second'
            nodes.append({'language': 'en', 'text': value})
        else:
            token=match.group('latin')
            # 常見普通英文詞保留在原句；縮寫、複合詞與識別符保留獨立英文讀法。
            if token.islower() and token in lexicon() and not initialism(token):
                pending+=' '+token+' '
            else:
                flush();nodes.append({'language':'en','text':english_words(token,normalize_en)})
        cursor=match.end()
    pending+=text[cursor:];flush()
    # 英文列舉合併為一句，避免合成只有「和」或單一標點的碎片。
    joiners={',':', ', '、':', ', '，':', ', '和':' and ', '與':' and ', '与':' and ', '及':' and ', '或':' or ',
             '/':' slash ', '／':' slash ', '+':' plus ', '＋':' plus ', '→':' then ', 'and':' and ', 'or':' or '}
    merged=[];i=0
    while i<len(nodes):
        node=dict(nodes[i]);i+=1
        while node['language']=='en' and i<len(nodes):
            if nodes[i]['language']=='en':
                node['text']+=' '+nodes[i]['text'];i+=1
            elif i+1<len(nodes) and nodes[i]['text'].strip() in joiners and nodes[i+1]['language']=='en':
                node['text']+=joiners[nodes[i]['text'].strip()]+nodes[i+1]['text'];i+=2
            else:break
        if node['language']=='zh' and merged and merged[-1]['language']=='en':
            leading=re.match(r'^\s*([。！？!?；;，,：:、.])\s*',node['text'])
            if leading:
                merged[-1]['ending']=leading[1]
                node['text']=node['text'][leading.end():]
        ending=re.search(r'[。！？!?；;，,：:、.]\s*$',node['text'])
        node['ending']=ending[0].strip() if ending else ''
        if any(c.isalnum() for c in node['text']):
            merged.append(node)
        elif merged and node['ending']:
            # 縮寫後只有句號時，保留原句界給合成／停頓；不能當成空片段丟掉。
            merged[-1]['ending']=node['ending']
    return merged
