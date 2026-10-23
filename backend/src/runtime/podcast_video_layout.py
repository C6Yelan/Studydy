"""語意分鏡編譯成有限圖形與 v4 聚焦／動態群組；座標、時間與文字容量由可信程式決定。"""

import re

from .podcast_video_plan import (
    KEY_TERM_LIMIT,
    SAFE_BOTTOM,
    V4_PAGE_LIMIT,
    object_schema,
    term_at,
    term_width,
    validate_plan,
    validate_visual_timing,
)


# 容納完整技術名稱與短句；能否放入畫面仍由實際字型量測決定。
NODE_LABEL_LIMIT = 32
NODE_TEXT_LIMIT = 120
ANCHOR_LIMIT = 40
COLORS = ("teal", "blue", "orange")


def _line_text(limit, minimum=0):
    return {
        "type": "string",
        "minLength": minimum,
        "maxLength": limit,
        "pattern": r"^[^\r\n]*$",
    }


def section_page_limit(section_count):
    """每段最多 3 頁，且全集不超過 V4_PAGE_LIMIT；由 schema 保證，不需事後合併。"""
    return max(1, min(3, V4_PAGE_LIMIT // section_count))


def semantic_schema(section_count):
    # anchor／key_terms 只是逐字引用；能否採用由 compile_layout 對講稿與節點文字核對。
    spoken = {
        "anchor": _line_text(ANCHOR_LIMIT, 1),
        "key_terms": {
            "type": "array",
            "minItems": 0,
            "maxItems": 2,
            "items": _line_text(2 * KEY_TERM_LIMIT, 1),
        },
    }
    variants = []
    for layout in ("concept", "comparison", "flow", "exchange"):
        node = object_schema(
            {
                "label": _line_text(NODE_LABEL_LIMIT, 1),
                "text": {"type": "string", "enum": [""]},
            }
            if layout == "exchange"
            else {
                "label": _line_text(NODE_LABEL_LIMIT, 1),
                "text": _line_text(NODE_TEXT_LIMIT),
                **spoken,
            }
        )
        relation = object_schema(
            {
                "source": {"type": "integer", "minimum": 0, "maximum": 1},
                "target": {"type": "integer", "minimum": 0, "maximum": 1},
                "label": _line_text(36, 1),
                **spoken,
            }
        )
        low, high = (
            (2, 2)
            if layout == "exchange"
            else (2, 3)
            if layout == "comparison"
            else (1, 6)
        )
        variants.append(
            object_schema(
                {
                    "layout": {"type": "string", "enum": [layout]},
                    "title": {"type": "string", "minLength": 1, "maxLength": 28},
                    # 同段第 2 頁起的換頁位置；第一頁從段首開始，留空即可。
                    "anchor": _line_text(ANCHOR_LIMIT),
                    "nodes": {
                        "type": "array",
                        "minItems": low,
                        "maxItems": high,
                        "items": node,
                    },
                    "relations": {
                        "type": "array",
                        # 只有一則訊息時改用 concept，避免整頁只剩一條箭頭。
                        "minItems": 2 if layout == "exchange" else 0,
                        "maxItems": 6 if layout == "exchange" else 0,
                        "items": relation,
                    },
                }
            )
        )
    section = object_schema(
        {
            "pages": {
                "type": "array",
                "minItems": 1,
                "maxItems": section_page_limit(section_count),
                "items": {"anyOf": variants},
            }
        }
    )
    return object_schema(
        {
            "sections": {
                "type": "array",
                "minItems": section_count,
                "maxItems": section_count,
                "items": section,
            }
        }
    )


def _element(text, x, y, w, h, cue, color="ink", kind="text", maximum=42):
    from .podcast_video_render import lines, font, line_step

    padding = 32 if kind == "box" else 0
    size = next(
        (
            s
            for s in (64, 52, 42, 34, 28)
            if s <= maximum
            and len(lines(text, s, w - padding)) * line_step(s, True) <= h - padding
            and all(
                font(s).getlength(line) <= w - padding
                for line in lines(text, s, w - padding)
            )
        ),
        None,
    )
    if size is None:
        raise ValueError(
            "VIDEO_LAYOUT_INVALID:node text exceeds its reading area; shorten the label or explanation without dropping necessary conditions"
        )
    return {
        "kind": kind,
        "cue_index": cue,
        "text": text,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "size": size,
        "color": color,
        "filled": False,
    }


def _fitted(text, x, y, w, h, cue, color, maximum):
    """文字元素只占實際行高，讓下一段緊接排列；字級仍由 _element 量測。"""
    from .podcast_video_render import lines, line_step

    element = _element(text, x, y, w, h, cue, color, "text", maximum)
    element["h"] = len(lines(text, element["size"], w)) * line_step(
        element["size"], True
    )
    return element


def _cell(node, x, y, w, h, cue, color, maximum):
    """卡片內的一個節點：標題字級由大到小試，留給說明的高度足夠才採用；都放不下才拒絕。"""
    for size in (64, 52, 42, 34, 28):
        if size > maximum[0]:
            continue
        try:
            label = _fitted(node["label"], x, y, w, h, cue, color, size)
            if not node["text"]:
                return label, None
            top = label["y"] + label["h"] + 24
            body = _fitted(
                node["text"], x, top, w, y + h - top, cue, "ink", min(size, maximum[1])
            )
            return label, body
        except ValueError:
            continue
    raise ValueError(
        "VIDEO_LAYOUT_INVALID:node text exceeds its reading area; shorten the label or explanation without dropping necessary conditions"
    )


def _line(x, y, w, h, cue, color="muted", kind="line"):
    return {
        "kind": kind,
        "cue_index": cue,
        "text": "",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "size": 28,
        "color": color,
        "filled": False,
    }


def _slots(timeline, start, end):
    """頁內可用的實測起點：每個 teaching cue 開頭與其中的字幕開頭，附講稿字元位置。"""
    segments = timeline["segments"]
    page_start, page_end = segments[start]["start"], segments[end]["end"]
    offset = sum(len(s["text"].replace("\n", "")) for s in segments[:start])
    slots = []
    narration = ""
    for cue in range(start, end + 1):
        slots.append((segments[cue]["start"], len(narration), cue, None))
        narration += segments[cue]["text"].replace("\n", "")
    position = 0
    for index, caption in enumerate(timeline.get("captions", [])):
        here = position - offset
        position += len(caption["text"].replace("\n", ""))
        if not page_start < caption["start"] < page_end:
            continue
        cue = next(
            c
            for c in range(start, end + 1)
            if segments[c]["start"] <= caption["start"] < segments[c]["end"]
        )
        slots.append((caption["start"], here, cue, index))
    unique = {}
    for slot in sorted(slots, key=lambda s: (s[0], s[3] is not None)):
        unique.setdefault(slot[0], slot)
    return narration, list(unique.values())


def _anchor_slot(anchor, narration, slots, limit=None):
    """anchor 必須逐字出現在本段講稿且落在本頁範圍；否則回傳 None，由版面退回。"""
    at = narration.find(anchor) if isinstance(anchor, str) and anchor else -1
    if at < 0 or at < slots[0][1] or (limit is not None and at >= limit):
        return None
    return max(i for i, slot in enumerate(slots) if slot[1] <= at)


def _split(pages, narration, slots):
    """段內換頁位置（段內實測起點索引）：第一頁從段首開始，第 2 頁起採用逐字 anchor。

    對不到、倒退、不在本段或擠掉後頁所需起點的 anchor 不採用，改放在前後已定換頁點之間
    居中的實測起點。都不產生新秒數；呼叫前已用 _merge_tail 讓頁數不超過實測起點數。
    """
    count = len(pages)
    bounds = [0] + [None] * (count - 1)
    last = 0
    for k in range(1, count):
        w = _anchor_slot(pages[k]["anchor"], narration, slots)
        if w is not None and bounds[last] + (k - last) <= w <= len(slots) - (count - k):
            bounds[k], last = w, k
    known = [k for k, b in enumerate(bounds) if b is not None] + [count]
    for a, b in zip(known, known[1:]):
        stop = bounds[b] if b < count else len(slots)
        for m in range(a + 1, b):
            bounds[m] = bounds[a] + (m - a) * (stop - bounds[a]) // (b - a)
    return bounds


BARE_NUMBER = re.compile(r"[-−+]?[\d.,]+%?")


def _key_terms(terms, body, label):
    """只保留逐字出自卡片文字的短詞；標該詞第一次出現的位置，不標整句或整個標籤。"""
    if not isinstance(terms, list):
        raise ValueError("VIDEO_STORYBOARD_INVALID:key_terms must be a list")
    marks = []
    for term in terms[:2]:
        if (
            not isinstance(term, str)
            or not term
            or term != term.strip()
            or term_width(term) > KEY_TERM_LIMIT
            # 單一字母／符號或單獨數值即使逐字出現也不是值得標的重點。
            or len(term) < 2
            or BARE_NUMBER.fullmatch(term)
            or "\n" in term
            or "\r" in term
        ):
            continue
        for target, text in (("body", body), ("label", label)):
            at = term_at(text, term)
            if at >= 0 and term != text:
                break
        else:
            continue
        if any(
            t == target and max(at, s) < min(at + len(term), s + len(o))
            for t, o, s in marks
        ):
            continue
        marks.append((target, term, at))
    return [(target, term) for target, term, _ in marks]


def _as_nodes(page):
    """把一頁的內容轉成聚焦卡節點；exchange 訊息保留方向，寫成「傳送方 → 接收方」。"""
    if page["layout"] != "exchange":
        return [dict(node) for node in page["nodes"]]
    names = [node["label"] for node in page["nodes"]]
    return [
        {
            "label": r["label"],
            "text": f"{names[r['source']]} → {names[r['target']]}",
            "anchor": r["anchor"],
            "key_terms": r["key_terms"],
        }
        for r in page["relations"]
    ]


def _merge_tail(pages, capacity):
    """段內實測起點比頁數少時，放不下獨立起點的頁把節點併入前一頁，不丟內容。

    併入後沿用前一頁的標題與 anchor；同為 concept 或同為 flow 時保留版型，其餘改用 concept
    聚焦卡。節點過多放不下時，由版面量測照常回報 VIDEO_LAYOUT_INVALID。
    """
    if len(pages) <= capacity:
        return pages
    head, tail = pages[: capacity - 1], pages[capacity - 1 :]
    kinds = {p["layout"] for p in tail}
    layout = (
        tail[0]["layout"]
        if len(kinds) == 1 and kinds <= {"concept", "flow"}
        else "concept"
    )
    merged = {
        "layout": layout,
        "title": tail[0]["title"],
        "anchor": tail[0]["anchor"],
        "nodes": [node for p in tail for node in _as_nodes(p)],
        "relations": [],
    }
    return head + [merged]


def _check_section(section, p, start, cues):
    where = f"VIDEO_STORYBOARD_INVALID:section={p}"
    if (
        set(section) != {"start_cue", "end_cue", "pages"}
        or type(section["start_cue"]) is not int
        or type(section["end_cue"]) is not int
        or section["start_cue"] != start
        or not start <= section["end_cue"] < len(cues)
        or not isinstance(section["pages"], list)
        or not 1 <= len(section["pages"]) <= 3
    ):
        raise ValueError(where + ", invalid section range or page count")
    for i, spec in enumerate(section["pages"]):
        _check_page(spec, f"{where},page={i}")


def _check_page(spec, where):
    if set(spec) != {"layout", "title", "anchor", "nodes", "relations"}:
        raise ValueError(where + ", invalid semantic page fields")
    kind = spec["layout"]
    nodes, relations = spec["nodes"], spec["relations"]
    if (
        not isinstance(spec["title"], str)
        or not 1 <= len(spec["title"]) <= 28
        or kind not in ("concept", "comparison", "flow", "exchange")
        or not isinstance(nodes, list)
        or not 1 <= len(nodes) <= 6
        or not isinstance(relations, list)
        or len(relations) > 6
    ):
        raise ValueError(where + ", invalid page range, layout or item count")
    if kind == "exchange" and (len(nodes) != 2 or len(relations) < 2):
        raise ValueError(
            where
            + ", exchange requires two participants and at least two ordered messages"
        )
    if kind == "comparison" and not 2 <= len(nodes) <= 3:
        raise ValueError(where + ", comparison requires two or three groups")
    if kind != "exchange" and relations:
        raise ValueError(
            where
            + ", only exchange has explicit messages; flow steps are connected in order"
        )
    spoken = {"anchor", "key_terms"}
    for i, node in enumerate(nodes):
        if (
            set(node)
            != ({"label", "text"} if kind == "exchange" else {"label", "text"} | spoken)
            or not isinstance(node["label"], str)
            or not 1 <= len(node["label"]) <= NODE_LABEL_LIMIT
            or not isinstance(node["text"], str)
            or len(node["text"]) > NODE_TEXT_LIMIT
            or any(c in node["label"] + node["text"] for c in "\r\n")
            or (kind == "exchange" and node["text"])
        ):
            raise ValueError(
                where
                + f", node={i}: provide a short label and explanation; exchange participants use empty text"
            )
    for i, relation in enumerate(relations):
        if (
            set(relation) != {"source", "target", "label"} | spoken
            or any(
                type(relation[k]) is not int or not 0 <= relation[k] < len(nodes)
                for k in ("source", "target")
            )
            or relation["source"] == relation["target"]
            or not isinstance(relation["label"], str)
            or not 1 <= len(relation["label"]) <= 36
            or any(c in relation["label"] for c in "\r\n")
        ):
            raise ValueError(where + f", relation={i}: invalid participant or label")
    return where


def _schedule(kind, items, narration, slots, limit):
    """把逐字 anchor 換成頁內實測起點索引；不產生新秒數。"""
    wanted = [_anchor_slot(item["anchor"], narration, slots, limit) for item in items]
    if kind == "comparison":
        return [0 if w is None else w for w in wanted]
    if kind == "exchange":
        # 訊息方向依講述順序呈現；對不到或倒序的 anchor 與前一則同時出現。
        result = []
        for w in wanted:
            result.append(max(0 if w is None else w, result[-1] if result else 0))
        return result
    # 聚焦版面一次只亮一張卡：第一張從頁首開始，其後各占一個較晚的實測起點。
    if len(slots) < len(items):
        # 字幕只有段落粒度時實測起點可能不足；依序把相鄰節點併入同一張卡，不整份失敗。
        return [k * len(slots) // len(items) for k in range(len(items))]
    result = [0]
    for k, w in enumerate(wanted[1:], 1):
        earliest = result[-1] + 1
        result.append(
            min(
                earliest if w is None or w < earliest else w,
                len(slots) - (len(items) - k),
            )
        )
    return result


def _anchor(slot):
    return {
        "start_cue": slot[2],
        **({"caption_index": slot[3]} if slot[3] is not None else {}),
    }


def compile_layout(design, cues, timeline):
    """只編排模型明示的節點／關係，不自行新增箭頭、推論或文字。"""
    try:
        if set(design) != {"sections"} or not 1 <= len(design["sections"]) <= 12:
            raise ValueError("VIDEO_STORYBOARD_INVALID:expected 1..12 sections")
        specs = []
        next_cue = 0
        for p, section in enumerate(design["sections"]):
            _check_section(section, p, next_cue, cues)
            narration, section_slots = _slots(
                timeline, section["start_cue"], section["end_cue"]
            )
            section_pages = _merge_tail(section["pages"], len(section_slots))
            bounds = _split(section_pages, narration, section_slots)
            for k, first in enumerate(bounds):
                stop = bounds[k + 1] if k + 1 < len(bounds) else len(section_slots)
                following = section_slots[stop] if stop < len(section_slots) else None
                specs.append(
                    (
                        section_pages[k],
                        narration,
                        section_slots[first:stop],
                        following[1] if following else None,
                        # 下一頁從字幕開始時共用該 cue；從 cue 開頭開始時本頁止於前一個 cue。
                        section["end_cue"]
                        if following is None
                        else following[2] - (following[3] is None),
                    )
                )
            next_cue = section["end_cue"] + 1
        if len(specs) > V4_PAGE_LIMIT:
            raise ValueError(
                f"VIDEO_STORYBOARD_INVALID:at most {V4_PAGE_LIMIT} pages in total"
            )
        pages = []
        for spec, narration, slots, limit, end in specs:
            kind = spec["layout"]
            nodes, relations = spec["nodes"], spec["relations"]
            start = slots[0][2]
            page = {
                "title": spec["title"],
                **_anchor(slots[0]),
                "end_cue": end,
                "elements": [],
                "emphasis": [],
                "reveal": [],
            }
            elements = page["elements"]

            def add(element):
                elements.append(element)
                return len(elements) - 1

            if kind in ("concept", "flow"):
                chosen = _schedule(kind, nodes, narration, slots, limit)
                steps = []
                row = min(130, (SAFE_BOTTOM - 200) // len(nodes))
                for k, s in enumerate(chosen):
                    if not steps or steps[-1][0] != s:
                        steps.append((s, []))
                    steps[-1][1].append(k)
                page["spotlight"] = {"layout": kind, "steps": []}
                for s, members in steps:
                    slot = slots[s]
                    cue = slot[2]
                    card = [
                        add(
                            _line(
                                700,
                                200,
                                1110,
                                SAFE_BOTTOM - 200,
                                cue,
                                COLORS[members[0] % 3],
                                "box",
                            )
                        )
                    ]
                    rails = []
                    # 實測起點不足而共用一張卡時，卡內改成兩欄小格，標題與說明一起找放得下的字級。
                    columns = 1 if len(members) == 1 else 2
                    rows = -(-len(members) // columns)
                    w = (990 - 40 * (columns - 1)) // columns
                    h = (SAFE_BOTTOM - 40 - 248 - 24 * (rows - 1)) // rows
                    for i, k in enumerate(members):
                        node, color = nodes[k], COLORS[k % 3]
                        rail = _fitted(
                            node["label"], 174, 0, 456, row - 16, cue, color, 34
                        )
                        rail["y"] = 200 + k * row + (row - 16 - rail["h"]) // 2
                        rails.append(add(rail))
                        r, c = divmod(i, columns)
                        label, body = _cell(
                            node,
                            760 + c * (w + 40),
                            248 + r * (h + 24),
                            w,
                            h,
                            cue,
                            color,
                            (64, 52) if len(members) == 1 else (42, 42),
                        )
                        card.append(add(label))
                        targets = {"label": (node["label"], card[-1])}
                        if body:
                            card.append(add(body))
                            targets["body"] = (node["text"], card[-1])
                        _highlight(page, node, targets, slot, color, end)
                    page["spotlight"]["steps"].append(
                        {"rails": rails, "card": card, **_anchor(slot)}
                    )
            elif kind == "comparison":
                chosen = _schedule(kind, nodes, narration, slots, limit)
                groups = []
                w = (1720 - (len(nodes) - 1) * 70) // len(nodes)
                for i, (node, s) in enumerate(zip(nodes, chosen)):
                    slot, color = slots[s], COLORS[i % 3]
                    x = 100 + i * (w + 70)
                    label = _fitted(node["label"], x, 230, w, 230, slot[2], color, 52)
                    group = [add(label)]
                    targets = {"label": (node["label"], group[0])}
                    if node["text"]:
                        top = label["y"] + label["h"] + 20
                        group.append(
                            add(
                                _fitted(
                                    node["text"],
                                    x,
                                    top,
                                    w,
                                    SAFE_BOTTOM - top,
                                    slot[2],
                                    "ink",
                                    42,
                                )
                            )
                        )
                        targets["body"] = (node["text"], group[1])
                    group.append(add(_line(x, 200, w, 0, slot[2], color)))
                    groups.append({"elements": group, **_anchor(slot)})
                    _highlight(page, node, targets, slot, color, end)
                page["motion"] = {"layout": kind, "groups": groups}
            else:
                chosen = _schedule(kind, relations, narration, slots, limit)
                groups = []
                centers = []
                for i, node in enumerate(nodes):
                    x = 140 + i * 1140
                    group = [
                        add(
                            _element(
                                node["label"],
                                x,
                                190,
                                500,
                                100,
                                start,
                                COLORS[i],
                                "box",
                                42,
                            )
                        ),
                        add(_line(x + 250, 300, 0, SAFE_BOTTOM - 10 - 300, start)),
                    ]
                    centers.append(x + 250)
                    groups.append({"elements": group, **_anchor(slots[0])})
                step = 440 // max(1, len(relations) - 1)
                for i, (edge, s) in enumerate(zip(relations, chosen)):
                    slot = slots[s]
                    y = 370 + i * step
                    label = add(
                        _element(
                            edge["label"],
                            420,
                            y - 47,
                            1080,
                            42,
                            slot[2],
                            "ink",
                            maximum=34,
                        )
                    )
                    x1, x2 = centers[edge["source"]], centers[edge["target"]]
                    group = [
                        label,
                        add(_line(x1, y, x2 - x1, 0, slot[2], "ink", "arrow")),
                    ]
                    groups.append(
                        {
                            "elements": group,
                            "source": edge["source"],
                            "target": edge["target"],
                            **_anchor(slot),
                        }
                    )
                    if s:
                        page["reveal"].append({"elements": group, **_anchor(slot)})
                    _highlight(
                        page,
                        edge,
                        {"body": (edge["label"], label), "label": ("", label)},
                        slot,
                        "orange",
                        end,
                    )
                page["motion"] = {"layout": kind, "groups": groups}
            pages.append(page)
        result = validate_plan(
            {"schema": "podcast-storyboard/v4", "pages": pages}, cues
        )
        validate_visual_timing(result, timeline)
        return result
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(
            "VIDEO_STORYBOARD_INVALID:invalid semantic layout fields"
        ) from error


def _highlight(page, item, targets, slot, color, end):
    body, label = targets.get("body", ("", None)), targets["label"]
    for target, term in _key_terms(item["key_terms"], body[0], label[0]):
        page["emphasis"].append(
            {
                "element_index": (body if target == "body" else label)[1],
                "end_cue": end,
                "kind": "highlight",
                "quote": term,
                "color": color,
                **_anchor(slot),
            }
        )
