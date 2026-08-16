"""SimHash 64-bit 近似去重（设计文档 §4.2 去重）。

跨平台相同岗位的语义去重：精确去重（source+source_id）之外，
对 title/company/city 文本计算 SimHash 指纹，汉明距 ≤ 3 判定为近似重复。
去重准确率目标 ≥ 95%（设计文档 §4.2 指标）。

中文无需分词器：英文单词/数字按词提取，中文按连续片段（≥2 字）提取，
以 token 出现频次加权投票生成 64-bit 指纹。
"""

import hashlib
import re

# 指纹位数与近似判定阈值（设计文档 §4.2：SimHash 64-bit，汉明距 ≤ 3）
_BITS = 64
DEFAULT_HAMMING_THRESHOLD = 3

# token 提取：英文单词/数字，或连续中文片段（≥2 字，过滤单字噪声）
_WORD_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}")


def _tokenize(text: str) -> list[str]:
    """提取词 token。"""
    return _WORD_RE.findall(text.lower())


def _token_hash(token: str) -> int:
    """token → 64-bit 哈希（md5 取前 8 字节）。"""
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:8], "big")




def simhash64(text: str) -> int:
    """计算文本的 64-bit SimHash 指纹（pipelines 采集管线语义去重使用）。

    对每个 token 哈希，按位加权投票（该位为 1 +1，为 0 -1），
    最终指纹的每个 bit 取投票符号。空文本或无 token 时返回 0。
    """
    tokens = _tokenize(text)
    if not tokens:
        return 0

    weights = [0] * _BITS
    for token in tokens:
        h = _token_hash(token)
        for i in range(_BITS):
            weights[i] += 1 if (h >> i) & 1 else -1

    fingerprint = 0
    for i in range(_BITS):
        if weights[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """两个 64-bit 指纹的海明距离（异或后统计置位 bit 数）。"""
    return (a ^ b).bit_count()




def is_duplicate(a: int, b: int, threshold: int = DEFAULT_HAMMING_THRESHOLD) -> bool:
    """判定两个指纹是否近似重复（汉明距 ≤ threshold）。"""
    return hamming_distance(a, b) <= threshold


def find_similar_pairs(
    records: list[tuple[str, int]],
    threshold: int = DEFAULT_HAMMING_THRESHOLD,
) -> list[tuple[str, str]]:
    """批量查找近似重复对（分桶索引，避免全量 O(n²) 两两比较）。

    抽屉原理：64-bit 指纹分 4 块（各 16 bit），汉明距 ≤ threshold(3) 的两指纹
    必然在至少一个 16-bit 块上完全相同（若每块都不同则 ≥ 4 位不同）。
    故按 (块号, 块值) 分桶，仅比较同桶内的候选对；记录量级为万级时
    比较量从 O(n²) 降为近 O(n·k)。

    Args:
        records: [(record_id, simhash), ...]
        threshold: 汉明距阈值（>16 时抽屉原理不成立，退化为全量比较）

    Returns:
        近似重复记录 ID 对列表 [(id_a, id_b)]（a < b 顺序）
    """
    if len(records) < 2:
        return []

    block_bits = _BITS // 4
    if threshold >= block_bits:
        # 阈值 ≥ 块宽时无法保证某块完全相同，退回全量比较
        pairs: list[tuple[str, str]] = []
        for i in range(len(records)):
            for j in range(i + 1, len(records)):
                if hamming_distance(records[i][1], records[j][1]) <= threshold:
                    pairs.append((records[i][0], records[j][0]))
        return pairs

    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_, fp) in enumerate(records):
        for b in range(4):
            block = (fp >> (b * block_bits)) & ((1 << block_bits) - 1)
            buckets.setdefault((b, block), []).append(idx)

    pairs = []
    seen: set[tuple[int, int]] = set()
    for indices in buckets.values():
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                a, b = indices[i], indices[j]
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                if hamming_distance(records[a][1], records[b][1]) <= threshold:
                    pairs.append((records[a][0], records[b][0]))
    return pairs
