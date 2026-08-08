"""M1-04 结构化切分：按 token 切分，保留 section_path，含 overlap 和 content_hash。

切分参数（PoC 起点，需在评估集上调参）：
  chunk_size = 700 token（规格 600-800）
  overlap = 100 token（规格 80-120）

每个 chunk 包含：
  - section_path：从解析器继承的标题层级
  - content：切分后的文本
  - token_count：tiktoken 计数
  - content_hash：sha256 去重指纹
  - chunk_index：文档内顺序
"""

from __future__ import annotations

import hashlib

import tiktoken

from ekb_api.parsing import ParsedSection

# PoC 起始参数，需在评估集上调参（RAG 治理方案 4.1）。
CHUNK_SIZE_TOKENS = 700
OVERLAP_TOKENS = 100


class ChunkResult:
    """切分产物：包含 chunk 元数据和内容。"""

    def __init__(
        self,
        chunk_index: int,
        section_path: list[str],
        content: str,
        token_count: int,
        content_hash: str,
    ) -> None:
        self.chunk_index = chunk_index
        self.section_path = section_path
        self.content = content
        self.token_count = token_count
        self.content_hash = content_hash


def _get_encoder() -> tiktoken.Encoding:
    """获取 tiktoken 编码器，cl100k_base 兼容中英文。"""
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str, encoder: tiktoken.Encoding) -> int:
    return len(encoder.encode(text))


def _hash_content(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def split_sections(sections: list[ParsedSection]) -> list[ChunkResult]:
    """将解析段落按 token 切分为 chunk，保留 section_path。

    切分策略：
    1. 每个段落独立切分，section_path 继承到 chunk。
    2. 段落内按 chunk_size 切分，chunk 间保留 overlap 个 token 的重叠。
    3. 段落间不重叠（section_path 不同，重叠无语义意义）。
    """
    encoder = _get_encoder()
    results: list[ChunkResult] = []
    chunk_index = 0

    for section in sections:
        tokens = encoder.encode(section.content)
        if not tokens:
            continue

        start = 0
        while start < len(tokens):
            end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = encoder.decode(chunk_tokens)

            results.append(
                ChunkResult(
                    chunk_index=chunk_index,
                    section_path=list(section.section_path),
                    content=chunk_text,
                    token_count=len(chunk_tokens),
                    content_hash=_hash_content(chunk_text),
                )
            )
            chunk_index += 1

            if end >= len(tokens):
                break
            start = end - OVERLAP_TOKENS

    return results
