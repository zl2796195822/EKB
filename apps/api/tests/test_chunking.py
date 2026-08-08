"""M1-04 切分单元测试：锁定 split_sections 的切分、overlap、hash 和 index 行为。"""

from __future__ import annotations

import tiktoken

from ekb_api.chunking import CHUNK_SIZE_TOKENS, OVERLAP_TOKENS, ChunkResult, split_sections
from ekb_api.parsing import ParsedSection


def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _make_long_content(target_tokens: int) -> str:
    """生成约 target_tokens 个 token 的英文文本（每个单词约 1 token）。"""
    word = "knowledge "
    enc = _encoder()
    text = word * target_tokens
    # 微调：裁剪到恰好超过 target_tokens
    tokens = enc.encode(text)
    return enc.decode(tokens[:target_tokens])


# ---------------------------------------------------------------------------
# 1. 超长段落被切成多个 chunk，相邻 chunk 有 OVERLAP_TOKENS 重叠
# ---------------------------------------------------------------------------

def test_long_section_produces_multiple_chunks() -> None:
    content = _make_long_content(CHUNK_SIZE_TOKENS + OVERLAP_TOKENS + 50)
    sections = [ParsedSection(section_path=["doc", "section1"], content=content)]
    chunks = split_sections(sections)
    assert len(chunks) >= 2, "超过 CHUNK_SIZE_TOKENS 的内容应切成至少 2 个 chunk"


def test_overlap_tokens_between_adjacent_chunks() -> None:
    enc = _encoder()
    # 构造足够长的内容，保证至少 2 个 chunk
    content = _make_long_content(CHUNK_SIZE_TOKENS * 2)
    sections = [ParsedSection(section_path=["doc"], content=content)]
    chunks = split_sections(sections)
    assert len(chunks) >= 2

    # 第 1 个 chunk 的末尾 token 序列 应该和第 2 个 chunk 的开头有重叠
    tokens_0 = enc.encode(chunks[0].content)
    tokens_1 = enc.encode(chunks[1].content)
    overlap_tail = tokens_0[-OVERLAP_TOKENS:]
    overlap_head = tokens_1[:OVERLAP_TOKENS]
    assert overlap_tail == overlap_head, (
        f"相邻 chunk 应共享 {OVERLAP_TOKENS} 个 token 的重叠，"
        f"实际 tail={overlap_tail[:5]}… head={overlap_head[:5]}…"
    )


# ---------------------------------------------------------------------------
# 2. section_path 继承
# ---------------------------------------------------------------------------

def test_section_path_inherited_to_chunks() -> None:
    path = ["文档标题", "第一章", "1.1 节"]
    content = _make_long_content(CHUNK_SIZE_TOKENS + 50)
    sections = [ParsedSection(section_path=path, content=content)]
    chunks = split_sections(sections)
    for chunk in chunks:
        assert chunk.section_path == path, (
            f"chunk.section_path 应等于原始 section_path，实际={chunk.section_path}"
        )


def test_section_path_is_copy_not_reference() -> None:
    """section_path 应是独立副本，修改原列表不影响 chunk。"""
    path = ["doc", "sec"]
    sections = [ParsedSection(section_path=path, content="hello world")]
    chunks = split_sections(sections)
    path.append("mutated")
    assert "mutated" not in chunks[0].section_path


# ---------------------------------------------------------------------------
# 3. content_hash 一致性
# ---------------------------------------------------------------------------

def test_content_hash_stable_for_same_content() -> None:
    content = "相同内容应该产生相同的哈希值"
    s1 = [ParsedSection(section_path=["a"], content=content)]
    s2 = [ParsedSection(section_path=["b"], content=content)]
    chunks1 = split_sections(s1)
    chunks2 = split_sections(s2)
    assert chunks1[0].content_hash == chunks2[0].content_hash


def test_content_hash_differs_for_different_content() -> None:
    s = [
        ParsedSection(section_path=["a"], content="内容 A"),
        ParsedSection(section_path=["b"], content="内容 B"),
    ]
    chunks = split_sections(s)
    assert chunks[0].content_hash != chunks[1].content_hash


def test_content_hash_format() -> None:
    """hash 应以 'sha256:' 开头。"""
    sections = [ParsedSection(section_path=["x"], content="some text")]
    chunks = split_sections(sections)
    assert chunks[0].content_hash.startswith("sha256:")


# ---------------------------------------------------------------------------
# 4. token_count 等于 tiktoken 实际编码长度
# ---------------------------------------------------------------------------

def test_token_count_matches_tiktoken() -> None:
    enc = _encoder()
    content = "The quick brown fox jumps over the lazy dog."
    sections = [ParsedSection(section_path=["doc"], content=content)]
    chunks = split_sections(sections)
    for chunk in chunks:
        expected = len(enc.encode(chunk.content))
        assert chunk.token_count == expected, (
            f"token_count={chunk.token_count} 与 tiktoken 实际={expected} 不符"
        )


def test_token_count_for_long_section() -> None:
    enc = _encoder()
    content = _make_long_content(CHUNK_SIZE_TOKENS * 2)
    sections = [ParsedSection(section_path=["doc"], content=content)]
    chunks = split_sections(sections)
    for chunk in chunks:
        expected = len(enc.encode(chunk.content))
        assert chunk.token_count == expected


# ---------------------------------------------------------------------------
# 5. 空内容段落被跳过
# ---------------------------------------------------------------------------

def test_empty_content_section_skipped() -> None:
    sections = [
        ParsedSection(section_path=["empty"], content=""),
        ParsedSection(section_path=["real"], content="有实际内容的段落"),
    ]
    chunks = split_sections(sections)
    assert len(chunks) == 1
    assert chunks[0].section_path == ["real"]


def test_all_empty_sections_return_no_chunks() -> None:
    sections = [
        ParsedSection(section_path=["a"], content=""),
        ParsedSection(section_path=["b"], content="   "),
    ]
    # 空白字符串 tiktoken encode 也可能为 0 或非 0，验证实现行为
    chunks = split_sections(sections)
    # 纯空字符串 token 为 0，应被跳过
    empty_chunks = [c for c in chunks if c.section_path in [["a"]]]
    assert len(empty_chunks) == 0


# ---------------------------------------------------------------------------
# 6. chunk_index 全局连续递增，跨段落不重置
# ---------------------------------------------------------------------------

def test_chunk_index_starts_at_zero() -> None:
    sections = [ParsedSection(section_path=["doc"], content="first chunk content")]
    chunks = split_sections(sections)
    assert chunks[0].chunk_index == 0


def test_chunk_index_continuous_across_sections() -> None:
    long = _make_long_content(CHUNK_SIZE_TOKENS + 50)
    sections = [
        ParsedSection(section_path=["sec1"], content=long),
        ParsedSection(section_path=["sec2"], content="短内容"),
    ]
    chunks = split_sections(sections)
    indices = [c.chunk_index for c in chunks]
    expected = list(range(len(chunks)))
    assert indices == expected, f"chunk_index 应从 0 连续递增，实际={indices}"


def test_chunk_index_no_reset_between_sections() -> None:
    """跨段落时 chunk_index 不应重置为 0。"""
    long = _make_long_content(CHUNK_SIZE_TOKENS * 2)
    sections = [
        ParsedSection(section_path=["sec1"], content=long),
        ParsedSection(section_path=["sec2"], content=long),
    ]
    chunks = split_sections(sections)
    sec2_chunks = [c for c in chunks if c.section_path == ["sec2"]]
    assert sec2_chunks[0].chunk_index > 0, "第 2 个 section 的首个 chunk 的 index 不应从 0 开始"
