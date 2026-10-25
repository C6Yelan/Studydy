"""筆記公式的 LaTeX 驗證與匯出排版。

只接受網頁（KaTeX）與匯出（matplotlib mathtext）都能排版的指令子集；
解析不了的公式視為格式錯誤，不在任何一端以原文或錯誤訊息代替公式。
"""
from contextlib import contextmanager
from functools import lru_cache
from io import BytesIO
import re

# KaTeX 與 mathtext 都支援、且教材公式常用的指令；新增前須確認兩邊都能排版。
COMMANDS = frozenset("""
frac dfrac binom sqrt sum prod int oint lim log ln lg exp sin cos tan cot sec csc arcsin arccos arctan
sinh cosh tanh max min sup inf arg det Pr operatorname text mathrm mathbf mathit mathsf mathcal boldsymbol
times cdot div pm mp ast star circ bullet leq geq neq ne approx equiv sim simeq cong propto ll gg infty
partial nabla to rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow longrightarrow
mapsto uparrow downarrow in notin ni subset subseteq supset supseteq cup cap setminus emptyset varnothing
forall exists neg wedge vee ldots cdots vdots ddots dots quad qquad left right mid parallel perp angle
degree prime ell hbar Re Im hat bar tilde vec dot ddot overline underline widehat widetilde
lfloor rfloor lceil rceil langle rangle
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
""".split())
ESCAPES = frozenset(",;! {}%_#|")
MAX_LENGTH = 1000


class FormulaInvalid(ValueError):
    def __init__(self, latex, detail):
        super().__init__("NOTE_FORMULA_INVALID")
        self.latex = latex
        self.detail = detail


def problem(latex):
    """回傳公式不合格的原因；合格時回傳 None。"""
    if not latex.strip() or len(latex) > MAX_LENGTH:
        return "公式為空或過長"
    if re.search(r"(?<!\\)[$&%#]", latex) or "\n" in latex:
        return "不可含未跳脫的 $、&、%、# 或換行；百分號寫成 \\%"
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
    parser = _parser()  # 套件或字型設定缺失是部署錯誤，不能當成模型的格式錯誤。
    with _style():
        try:
            parser.parse(f"${latex}$")
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
    import matplotlib
    # 中文須在 \text{} 內；用匯出 PDF 同一套 Noto CJK，數學符號缺字時退回 STIX。
    font = "Noto Sans CJK TC"
    with matplotlib.rc_context({"mathtext.fontset": "custom", "mathtext.rm": font,
            "mathtext.it": font, "mathtext.bf": f"{font}:bold", "mathtext.sf": font,
            "mathtext.cal": font, "mathtext.tt": font, "mathtext.fallback": "stix"}):
        yield


def render_png(latex, *, size=12, dpi=300):
    """匯出用：回傳 PNG 與以 pt 計的顯示寬高。"""
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image
    _parser()
    stream = BytesIO()
    with _style():
        math_to_image(f"${latex}$", stream, prop=FontProperties(size=size), dpi=dpi, format="png")
    data = stream.getvalue()
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return data, width * 72 / dpi, height * 72 / dpi


_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉０１２３４５６７８９．", "0123456789" * 3 + ".")


def numbers(value):
    """字面值核對用：上下標與全形數字視同一般數字，其餘逐字比較。"""
    return set(re.findall(r"\d+(?:\.\d+)?", value.translate(_DIGITS)))
