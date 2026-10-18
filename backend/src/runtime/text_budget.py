"""以 o200k 估算既有提示容量；不冒充模型 tokenizer 或實際計費用量。"""

import json
from typing import Any


def estimate_o200k_tokens(text: str) -> int:
    # CLI 未提供精確 tokenizer；後端統一使用 o200k 估算，不冒充模型實測值。
    import tiktoken

    return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))


def estimate_prompt_tokens(prompt: str, request: dict[str, Any]) -> int:
    # 與現有 provider 的 prompt 序列化一致；不修改 Codex 或 provider。
    return estimate_o200k_tokens(
        prompt + "\nINPUT:\n" + json.dumps(request, ensure_ascii=False)
    )
