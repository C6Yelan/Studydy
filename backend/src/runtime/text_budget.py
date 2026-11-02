"""以 o200k 估算既有提示容量；不冒充模型 tokenizer 或實際計費用量。"""

import json
from typing import Any


def estimate_o200k_tokens(text: str) -> int:
    # 提示打包使用 o200k 估算；實際請求容量仍由 Gemma 服務的 tokenizer 核算。
    import tiktoken

    return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))


def estimate_prompt_tokens(prompt: str, request: dict[str, Any]) -> int:
    # 依文字模型請求的提示與 JSON 序列化格式估算輸入容量。
    return estimate_o200k_tokens(
        prompt + "\nINPUT:\n" + json.dumps(request, ensure_ascii=False)
    )
