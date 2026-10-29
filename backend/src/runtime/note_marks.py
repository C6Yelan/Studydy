"""匯出用的標記：把重新對應後的標記套到段落文字，讓各格式的段落輸出函式依 run 加上樣式。"""
from copy import deepcopy
from html import escape

COLORS = ("yellow", "green", "blue", "pink")
FILL = {"yellow": "FFF176", "green": "A5D6A7", "blue": "90CAF9", "pink": "F8BBD0"}
FLAGS = ("bold", "underline", "strikethrough")


class Marked(str):
    """帶樣式的文字；`runs` 為 [(文字, 螢光色或 None, 字樣集合)]，一般字串渲染不受影響。"""
    runs: list


def _runs(text, spans):
    points = sorted({0, len(text), *(p for s, e, _ in spans for p in (s, e))})
    runs = []
    for a, b in zip(points, points[1:]):
        covering = [style for s, e, style in spans if s <= a and b <= e]
        color = next((s for s in reversed(covering) if s in COLORS), None)  # 重疊時最後建立者優先
        runs.append((text[a:b], color, frozenset(s for s in covering if s in FLAGS)))
    return runs


def apply(paragraphs, highlights):
    """回傳套用標記後的段落副本；失配的標記不套到正文。"""
    placed = {}
    for h in highlights:
        if h["paragraph_id"] is not None:
            placed.setdefault((h["paragraph_id"], tuple(h["block_path"])), []).append((h["start"], h["end"], h["style"]))
    result = deepcopy(paragraphs)
    for p in result:
        for (pid, path), spans in placed.items():
            if pid != p["id"]:
                continue
            parent = p
            for key in path[:-1]:
                parent = parent[key]
            if path[-1] == "label":
                from .note_content import CALLOUT_LABELS
                parent["label"] = CALLOUT_LABELS[parent["style"]]
            text = Marked(parent[path[-1]])
            text.runs = _runs(text, spans)
            parent[path[-1]] = text
    return result


def html(text):
    """PDF（HTML）：非 Marked 文字等同 escape。"""
    if not isinstance(text, Marked):
        return escape(text)
    parts = []
    for value, color, flags in text.runs:
        out = escape(value)
        decorations = [d for f, d in (("underline", "underline"), ("strikethrough", "line-through")) if f in flags]
        style = (f"background-color:#{FILL[color]};" if color else "") + (
            f"text-decoration:{' '.join(decorations)};" if decorations else "")
        if style:
            out = f'<span style="{style}">{out}</span>'
        parts.append(f"<b>{out}</b>" if "bold" in flags else out)
    return "".join(parts)


def formula_fill(text):
    """排版成圖片的整條公式，以底色保留標記（字樣標記用黃色）。"""
    if isinstance(text, Marked):
        styles = [(color, flags) for _, color, flags in text.runs if color or flags]
        if styles:
            return FILL[next((color for color, _ in reversed(styles) if color), "yellow")]
    return None


def markdown(text, escaper):
    """Markdown 只帶粗體與刪除線；跳脫由呼叫端給（md_text）。"""
    if not isinstance(text, Marked):
        return escaper(text)
    parts = []
    for value, _, flags in text.runs:
        out = escaper(value)
        if out.strip():
            lead, core = out[:len(out) - len(out.lstrip())], out.strip()
            tail = out[len(lead) + len(core):]
            if "bold" in flags:
                core = f"**{core}**"
            if "strikethrough" in flags:
                core = f"~~{core}~~"
            out = lead + core + tail
        parts.append(out)
    return "".join(parts)


def docx_runs(paragraph, text, clean):
    """Word：每個 run 加字樣與螢光筆（Word 螢光筆僅 16 色，用 shading 填入指定顏色）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    for value, color, flags in (text.runs if isinstance(text, Marked) else [(text, None, frozenset())]):
        run = paragraph.add_run(clean(value))
        run.bold = True if "bold" in flags else None
        run.underline = True if "underline" in flags else None
        run.font.strike = True if "strikethrough" in flags else None
        if color:
            shading = OxmlElement("w:shd")
            shading.set(qn("w:val"), "clear")
            shading.set(qn("w:fill"), FILL[color])
            run._r.get_or_add_rPr().append(shading)
