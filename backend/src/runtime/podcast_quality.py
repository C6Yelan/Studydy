"""新講稿的篇幅預算與可定位的教學品質訊號；不改寫既有講稿。"""
from difflib import SequenceMatcher
from copy import deepcopy
import math
import re
import unicodedata


def _source_size(claims):
    # 英文來源不能按每個拉丁字母膨脹中文口述預算；同一來源長段被多個 claim
    # 引用也不應重複增加篇幅。這只估算容量，所有 claim 仍需獨立涵蓋／核對。
    previous = []; size = 0
    for claim in claims:
        tokens = re.findall(r'[A-Za-z0-9]+(?:[./_-][A-Za-z0-9]+)*|[^\s]', claim['text'])
        shared = set()
        for block in SequenceMatcher(None, previous, tokens, autojunk=False).get_matching_blocks():
            if block.size >= 12:
                shared.update(range(block.b, block.b + block.size))
        size += sum(2 if token.isascii() and token.isalnum() else 1
                    for i, token in enumerate(tokens) if i not in shared)
        previous.extend(tokens)
    return size


def content_budget(claims, delivery):
    # 以全部所選來源的文字量給空間；雙人不能再乘一份「對話膨脹」預算。
    size = _source_size(claims)
    concepts = len({c.get('concept_id', c.get('label')) for c in claims})
    beats = min(6, max(1, concepts, math.ceil(len(claims) / 2), math.ceil(size / 700)))
    characters = min(9600, max(240, math.ceil(size * 1.35) + 80))
    turns = beats * 2 + 1 if delivery == 'dialogue' else beats + 1
    return {'max_characters': characters, 'max_beats': beats, 'max_turns': turns}


def budget_issues(segments, budget):
    turns = [t for b in segments for t in b['turns']]
    actual = {'max_characters': sum(len(t['text']) for t in turns),
              'max_beats': len(segments), 'max_turns': len(turns)}
    return [{'code': 'content_budget', 'field': key, 'actual': count, 'limit': budget[key],
             'blocking': True, 'detail': '保留所有來源與必要條件，刪除重述、附和及非必要例子；不可截斷原文。'}
            for key, count in actual.items() if count > budget[key]]


def join_question_beats(segments):
    """純提問沒有獨立來源範圍時，與緊接的解答共用教學單位；不刪改任何發言。"""
    result = deepcopy(segments)
    i = 0
    while i+1 < len(result):
        current, following = result[i], result[i+1]
        turns = current['turns'] + following['turns']
        question_only = bool(current['turns']) and all(
            ''.join(p['text'] for p in t['parts']).strip().rstrip('」”"').endswith(('？', '?'))
            and all(not p['source_refs'] for p in t['parts']) for t in current['turns'])
        if question_only and len(turns) <= 12 and sum(len(p['text']) for t in turns for p in t['parts']) <= 3200:
            following['turns'] = turns
            result.pop(i)
        else:
            i += 1
    return result


def _plain(text):
    # 大小寫、數字與否定字仍有意義，不能把 MB / Mb 或相反命題當成相同內容。
    return ''.join(c for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def _substance(text):
    text = re.sub(r'^(?:所以|也就是說|也就是|換句話說|簡單說|對|沒錯|是的)[，,、：:\s]*', '', text.strip())
    text = re.sub(r'(?:對不對|對吧|是嗎|嗎)[？?。.!！\s]*$', '', text)
    return _plain(text)


def teaching_signals(segments):
    """確定無增量的附和／逐字重述直接修稿；相似度與輪替只供既有審查判讀。"""
    turns = [t for b in segments for t in b['turns']]
    signals = []
    texts = [_substance(t['text']) for t in turns]
    source_indices = lambda t: {r['source_index'] for p in t.get('parts', []) for r in p['source_refs']}
    all_sources = set().union(*(source_indices(t) for t in turns))
    covered = set()
    earlier = ''
    for i, turn in enumerate(turns):
        text = turn['text']; plain = _plain(text); value = texts[i]
        if plain in {'對', '沒錯', '是的', '嗯', '好的', '了解', '原來如此', '對沒錯'}:
            signals.append({'code': 'empty_confirmation', 'turn': i, 'blocking': True,
                            'detail': '這一輪只有確認，請移除或改成有實質資訊的介入。'})
        if i and len(value) >= 8 and value == texts[i-1]:
            signals.append({'code': 'restated_turn', 'turn': i, 'previous_turn': i-1, 'blocking': True,
                            'detail': '去掉確認語與問句語尾後，內容與上一輪相同，沒有推進理解。'})
        elif i and min(len(value), len(texts[i-1])) >= 12:
            ratio = SequenceMatcher(None, texts[i-1], value, autojunk=False).ratio()
            if ratio >= .72:
                signals.append({'code': 'adjacent_overlap', 'turn': i, 'previous_turn': i-1,
                                'similarity': round(ratio, 3), 'blocking': False,
                                'detail': '高度相似；核對是否真有新條件、修正或例子，不可只看共同術語就判錯。'})
        if (0 < i < len(turns)-1 and value != texts[i-1] and turns[i-1]['speaker'] != turn['speaker']
                and turns[i+1]['speaker'] == turns[i-1]['speaker']
                and re.match(r'^(?:所以|也就是說|也就是|換句話說)', text.strip())
                and re.search(r'[？?][」”"]?\s*$', text)
                and re.match(r'^(?:對|沒錯|是的|正是)(?:[，,。！!\s]|而且|也)', turns[i+1]['text'].strip())
                and not re.search(r'如果|假設|例如|換成|改成|為什麼|怎麼|如何|哪些|何時|什麼情況|我(?:原本|一直)?以為|誤以為', text)):
            question_grams = {value[j:j+2] for j in range(len(value)-1)}
            overlap = sum(g in texts[i-1] for g in question_grams) / max(1, len(question_grams))
            if overlap >= .4 and len(value) >= 8 and not source_indices(turn) - source_indices(turns[i-1]):
                signals.append({'code': 'confirmation_loop', 'turn': i, 'previous_turn': i-1,
                                'reply_turn': i+1, 'blocking': True,
                                'detail': '這個「所以……？」承接的是剛說過的內容，下一輪又先肯定；刪除無增量的確認，讓說明直接推進，或改成真正的新情境／疑惑。'})
        if (i and covered and covered == all_sources and re.match(r'^所以', text.strip())
                and re.search(r'[？?][」”"]?\s*$', text)
                and not re.search(r'如果|假設|例如|換成|改成|為什麼|怎麼|如何', text)):
            signals.append({'code': 'question_after_coverage', 'turn': i, 'blocking': False,
                            'detail': '所有來源都已講到後才提出這個確認／反向假設。核對前文是否已明確回答；不能為製造誤解而重講已交代的條件。來源重用本身不是錯。'})
        recap = re.search(r'(?:^|[。！？!?]\s*)(所以|總結|整理|回顧|最後|記住|重點是)', text.strip())
        if recap:
            offset = recap.start(1)
            ending = _substance(text.strip()[offset:])
            already = earlier + '\n' + _plain(text.strip()[:offset])
            if len(ending) >= 30:
                grams = {ending[j:j+3] for j in range(len(ending)-2)}
                repeated = sum(g in already for g in grams) / len(grams)
                if repeated >= .55:
                    signals.append({'code': 'repeated_recap', 'turn': i, 'text_offset': offset,
                                    'coverage': round(repeated, 3), 'blocking': ending in already,
                                    'detail': '收尾多處內容已講過；核對是否仍有必要整理或新判斷，否則刪除重述。'})
        earlier += '\n' + value
        covered.update(source_indices(turn))
    if len(turns) >= 6 and all(a['speaker'] != b['speaker'] for a, b in zip(turns, turns[1:])):
        signals.append({'code': 'regular_alternation', 'turns': list(range(len(turns))), 'blocking': False,
                        'detail': '整集固定輪替；核對問題是否有真實疑惑與新資訊，不要求刻意製造連續同角色。'})
    return signals
