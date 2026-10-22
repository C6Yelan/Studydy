from functools import lru_cache
import os
import re


@lru_cache
def font(size):
    from PIL import ImageFont

    return ImageFont.truetype(
        os.environ.get(
            "STUDYDY_VIDEO_FONT",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ),
        size,
    )


@lru_cache(maxsize=512)
def lines(text, size, width):
    # 自動換行時，句末標點跟隨前文、開括號跟隨後文；明示換行仍保留。
    closing = "，。！？；：、％）］｝〉》」』】〕…,.!?;:%)]}"
    opening = "（［｛〈《「『【〔([{"

    def units(tokens):
        result = []
        for token in tokens:
            if (
                result
                and token != "\n"
                and result[-1] != "\n"
                and (token[0] in closing or result[-1][-1] in opening)
            ):
                result[-1] += token
            else:
                result.append(token)
        return result

    result = []
    line = ""
    tokens = units(re.findall(r"[A-Za-z0-9][A-Za-z0-9_./+-]*|\n|.", text))
    for token in tokens:
        if token == "\n":
            result.append(line)
            line = ""
            continue
        # 縮寫、單位與識別符能放入一行時整詞移動；過長時才逐字拆分。
        parts = [token] if font(size).getlength(token) <= width else units(token)
        for part in parts:
            if line and font(size).getlength(line + part) > width:
                result.append(line)
                line = ""
            line += part
    result.append(line)
    return result


def line_step(size, spacious):
    """v4 大字卡片用 1.5 倍行距；舊分鏡維持原本固定 10px，已存影片的重繪結果不變。"""
    return size + size // 2 if spacious else size + 10
