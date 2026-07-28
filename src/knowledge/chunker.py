"""
文本分块器 — 将长文档切分为可索引的语义块

策略:
1. 段落切分（默认）: 按双换行切，每块最多 N 字符
2. 句子切分: 按句号/问号/感叹号切，保证语义完整
3. 滑动窗口: 块之间有重叠，避免切断上下文
"""
import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TextChunk:
    """文档分块"""
    chunk_id: str
    text: str
    title: str = ""           # 来源文档标题
    source: str = ""          # 来源文件名
    chunk_index: int = 0      # 在文档中的序号
    metadata: dict = field(default_factory=dict)


def chunk_document(
    content: str,
    title: str = "",
    source: str = "",
    chunk_size: int = 500,
    overlap: int = 50,
    strategy: str = "paragraph",
) -> list[TextChunk]:
    """
    将文档内容切分为多个块

    Args:
        content: 文档纯文本
        title: 文档标题
        source: 来源文件名
        chunk_size: 每块最大字符数
        overlap: 相邻块重叠字符数
        strategy: "paragraph" | "sentence" | "fixed"

    Returns:
        TextChunk 列表
    """
    if strategy == "sentence":
        segments = _split_by_sentence(content)
    elif strategy == "fixed":
        segments = [content]
    else:  # paragraph (default)
        segments = _split_by_paragraph(content)

    chunks = _merge_segments(segments, chunk_size)
    chunks = _add_overlap(chunks, overlap)

    result = []
    for i, text in enumerate(chunks):
        chunk_id = f"{source}_chunk_{i:04d}"
        result.append(TextChunk(
            chunk_id=chunk_id,
            text=text.strip(),
            title=title,
            source=source,
            chunk_index=i,
            metadata={"source": source, "title": title, "index": i},
        ))

    logger.info(f"分块完成: {source} → {len(result)} 块 (策略={strategy}, chunk_size={chunk_size})")
    return result


def _split_by_paragraph(text: str) -> list[str]:
    """按双换行/空行切分"""
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 按空行分割
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


def _split_by_sentence(text: str) -> list[str]:
    """按句子切分（中英文句号、问号、感叹号）"""
    # 匹配句子结尾（中英文标点 + 后跟空格或换行或字符串结尾）
    sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
    return [s.strip() for s in sentences if s.strip()]


def _merge_segments(segments: list[str], max_chars: int) -> list[str]:
    """合并短段落，拆分超长段落"""
    chunks = []
    current = ""

    for seg in segments:
        # 如果单段超长，先拆分它
        if len(seg) > max_chars * 1.5:
            # 先保存当前累积的
            if current.strip():
                chunks.append(current.strip())
                current = ""

            # 按固定长度拆超长段
            for i in range(0, len(seg), max_chars):
                chunks.append(seg[i:i + max_chars].strip())
            continue

        # 合并到当前块
        if len(current) + len(seg) + 1 <= max_chars:
            current += ("\n" + seg) if current else seg
        else:
            if current.strip():
                chunks.append(current.strip())
            current = seg

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _add_overlap(chunks: list[str], overlap: int) -> list[str]:
    """在相邻块之间添加重叠"""
    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    result = []
    for i, chunk in enumerate(chunks):
        if i > 0 and overlap > 0:
            # 从前一块末尾取 overlap 字符
            prev = chunks[i - 1]
            prev_tail = prev[-overlap:] if len(prev) > overlap else prev
            chunk = prev_tail + "\n...\n" + chunk
        result.append(chunk)

    return result
