"""筆記公式的 LaTeX 驗證與匯出排版。

只接受網頁（KaTeX）與匯出（matplotlib mathtext）都能排版的指令子集；
解析不了的公式視為格式錯誤，不在任何一端以原文或錯誤訊息代替公式。
"""
from contextlib import contextmanager
from functools import lru_cache
from io import BytesIO
import re
from threading import Lock

# KaTeX 與匯出都能排版的指令；範數左右定界符在匯出前等價轉為 mathtext 支援的雙豎線。
COMMANDS = frozenset("""
frac dfrac binom sqrt sum prod int oint lim log ln lg exp sin cos tan cot sec csc arcsin arccos arctan
sinh cosh tanh max min sup inf arg det Pr operatorname text mathrm mathbf mathit mathsf mathcal boldsymbol
times cdot div pm mp ast star circ bullet leq geq neq ne approx equiv sim simeq cong propto ll gg infty
partial nabla to rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow longrightarrow
mapsto uparrow downarrow in notin ni subset subseteq supset supseteq cup cap setminus emptyset varnothing
forall exists neg wedge vee ldots cdots vdots ddots dots quad qquad left right mid parallel perp angle
degree prime ell hbar Re Im hat bar tilde vec dot ddot overline underline widehat widetilde
lfloor rfloor lceil rceil langle rangle lVert rVert
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
""".split())
ESCAPES = frozenset(",;! {}%_#|")
MAX_LENGTH = 1000
_LAYOUT_LOCK = Lock()


def _mathtext_latex(latex):
    return re.sub(r"\\(?:lVert|rVert)(?![A-Za-z])", lambda _: r"\|", latex)


class FormulaInvalid(ValueError):
    def __init__(self, latex, detail, paragraph_index, block_index):
        super().__init__("NOTE_FORMULA_INVALID")
        self.latex = latex
        self.detail = detail
        self.paragraph_index = paragraph_index
        self.block_index = block_index


def problem(latex):
    """回傳公式不合格的原因；合格時回傳 None。"""
    if not latex.strip() or len(latex) > MAX_LENGTH:
        return "公式為空或過長"
    if re.search(r"[\x00-\x1f\x7f]", latex):
        return "公式不可含控制字元；JSON 中 LaTeX 的反斜線須正確跳脫"
    if re.search(r"\\(?:left|right)\s*\\mid(?![A-Za-z])", latex):
        return r"\mid 不是定界符；雙豎線用 \left\| ... \right\|，單豎線用 \left| ... \right|，須保留原文的豎線數量"
    if re.search(r"(?<!\\)[$&%#]", latex):
        return "不可含未跳脫的 $、&、%、#；百分號寫成 \\%"
    if re.search(r"(?<!\\)[_^][A-Za-z0-9]{2,}", latex):
        return "多字元上下標須使用大括號；命名變數與文字下標可用 \\mathrm{}"
    for match in re.finditer(r"\\([A-Za-z]+|.)", latex, re.S):
        name = match.group(1)
        if (name not in COMMANDS) if name[0].isalpha() else (name not in ESCAPES):
            return f"不支援的指令 \\{name}"
    depth = 0
    for match in re.finditer(r"\\.|[{}]", latex, re.S):
        token = match.group(0)
        depth += 1 if token == "{" else -1 if token == "}" else 0
        if depth < 0:
            return "大括號不成對"
    if depth:
        return "大括號不成對"
    if len(re.findall(r"\\left(?![A-Za-z])", latex)) != len(re.findall(r"\\right(?![A-Za-z])", latex)):
        return "\\left 與 \\right 不成對"
    with _style():
        parser = _parser()  # 套件或字型設定缺失是部署錯誤，不能當成模型的格式錯誤。
        try:
            parser.parse(f"${_mathtext_latex(latex)}$")
        except Exception:  # mathtext 以多種 pyparsing／ValueError 回報語法錯誤。
            return "LaTeX 語法無法解析"
    return None


@lru_cache(maxsize=1)
def _parser():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.mathtext import MathTextParser
    return MathTextParser("path")


@contextmanager
def _style():
    # 筆記驗證與兩個匯出執行緒會共用 mathtext parser／rcParams，必須一起鎖住排版及還原。
    with _LAYOUT_LOCK:
        import matplotlib
        font = "Noto Sans CJK TC"
        with matplotlib.rc_context({"mathtext.fontset": "custom", "mathtext.rm": font,
                "mathtext.it": font, "mathtext.bf": f"{font}:bold", "mathtext.sf": font,
                "mathtext.cal": font, "mathtext.tt": font, "mathtext.fallback": "stix"}):
            yield


def render_png(latex, *, size=12, dpi=300):
    """匯出用：回傳 PNG 與以 pt 計的顯示寬高。"""
    stream = BytesIO()
    with _style():
        _parser()
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import math_to_image
        math_to_image(f"${_mathtext_latex(latex)}$", stream, prop=FontProperties(size=size), dpi=dpi, format="png")
    data = stream.getvalue()
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return data, width * 72 / dpi, height * 72 / dpi


_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉０１２３４５６７８９．", "0123456789" * 3 + ".")


def numbers(value):
    """字面值核對用：上下標與全形數字視同一般數字，其餘逐字比較。"""
    return set(re.findall(r"\d+(?:\.\d+)?", value.translate(_DIGITS)))
