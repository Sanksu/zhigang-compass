"""SimHash 64-bit 近似去重（设计文档 §4.2 去重）。

跨平台相同岗位的语义去重：精确去重（source+source_id）之外，
对 title/company/city 文本计算 SimHash 指纹，汉明距 ≤ 运行时阈值
（configs/data_quality_thresholds.json，默认 3）判定为近似重复。
去重准确率目标 ≥ 95%（设计文档 §4.2 指标）。

中文无需分词器：英文单词/数字按词提取，中文按连续片段（≥2 字）提取，
以 token 出现频次加权投票生成 64-bit 指纹。
"""

import hashlib
import re
from typing import Iterable

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




def _resolve_threshold(threshold: int | None) -> int:
    """未显式传阈值时读取运行时配置（configs/data_quality_thresholds.json）。"""
    if threshold is None:
        from app.services.data_quality.thresholds import load_hamming_threshold

        return load_hamming_threshold()
    return threshold


def is_duplicate(a: int, b: int, threshold: int | None = None) -> bool:
    """判定两个指纹是否近似重复（汉明距 ≤ threshold，缺省取运行时配置）。

    去重判定是防劣质爬虫数据污染检索池的第一道闸：threshold 由
    ``configs/data_quality_thresholds.json`` 驱动，可运行时收紧（见该文件 _comment）。
    """
    return hamming_distance(a, b) <= _resolve_threshold(threshold)


def find_similar_pairs(
    records: list[tuple[str, int]],
    threshold: int | None = None,
) -> list[tuple[str, str]]:
    """批量查找近似重复对（分桶索引，避免全量 O(n²) 两两比较）。

    抽屉原理：64-bit 指纹分 4 块（各 16 bit），汉明距 ≤ threshold 的两指纹
    必然在至少一个 16-bit 块上完全相同（若每块都不同则 ≥ 4 位不同）。
    故按 (块号, 块值) 分桶，仅比较同桶内的候选对；记录量级为万级时
    比较量从 O(n²) 降为近 O(n·k)。

    Args:
        records: [(record_id, simhash), ...]
        threshold: 汉明距阈值（None 取运行时配置；>16 时抽屉原理不成立，
            退化为全量比较）

    Returns:
        近似重复记录 ID 对列表 [(id_a, id_b)]（a < b 顺序）
    """
    threshold = _resolve_threshold(threshold)
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


class SimHashIndex:
    """增量式 SimHash 近邻索引（P12 流式去重性能优化）。

    对比每次全量 ``find_similar_pairs``：流式数据持续写入时，历史记录每轮
    都被重复加载并两两比较，近邻比较量随数据量增长退化（接近 O(n²) 量级）。
    本索引只对**新增**记录做近邻检索：

      - ``add(record_id, fingerprint)`` 增量入桶。分块抽屉原理与
        ``find_similar_pairs`` 同源：64-bit 指纹分 4 块（各 16 bit），
        汉明距 ≤ 运行时阈值的两指纹必然共享至少一个 16-bit 块；
      - ``find_near(record_id, fingerprint)`` 仅查查询指纹命中的候选桶，
        候选再做汉明距校验，返回索引中与其近似重复的已入库记录 ID。

    近邻比较量从"每轮全量两两比较"降为"每新增一条仅比较同桶候选"，
    桶内候选数远小于全量记录数，索引随数据增长保持亚线性查询。
    索引条目可经 ``items()`` 导出持久化（如 Redis），``from_items`` 恢复。
    """

    def __init__(
        self,
        threshold: int | None = None,
        block_count: int = 4,
    ) -> None:
        threshold = _resolve_threshold(threshold)
        if threshold >= _BITS // block_count:
            raise ValueError(
                f"threshold({threshold}) must be < block_bits({_BITS // block_count}) "
                "for the drawer-principle guarantee"
            )
        self.threshold = threshold
        self.block_bits = _BITS // block_count
        self._block_count = block_count
        self._fingerprints: dict[str, int] = {}  # record_id -> fingerprint
        self._buckets: dict[tuple[int, int], list[str]] = {}

    def __len__(self) -> int:
        return len(self._fingerprints)

    def _bucket_keys(self, fingerprint: int) -> list[tuple[int, int]]:
        return [
            (b, (fingerprint >> (b * self.block_bits)) & ((1 << self.block_bits) - 1))
            for b in range(self._block_count)
        ]

    def add(self, record_id: str, fingerprint: int) -> None:
        """增量插入一条指纹到索引（重复 record_id 覆盖，保证重跑幂等）。"""
        rid = str(record_id)
        self._fingerprints[rid] = fingerprint
        for key in self._bucket_keys(fingerprint):
            bucket = self._buckets.setdefault(key, [])
            if rid not in bucket:
                bucket.append(rid)

    def find_near(self, record_id: str, fingerprint: int) -> list[str]:
        """返回索引中与给定指纹汉明距 ≤ threshold 的已入库记录 ID（不含自身）。

        仅检查查询指纹命中的候选桶（抽屉原理保证真近邻必在候选内），
        桶内候选做汉明距校验，避免全量扫描。
        """
        rid = str(record_id)
        candidates: set[str] = set()
        for key in self._bucket_keys(fingerprint):
            candidates.update(self._buckets.get(key, ()))
        candidates.discard(rid)

        near: list[str] = []
        for cid in candidates:
            stored = self._fingerprints.get(cid)
            if stored is not None and hamming_distance(fingerprint, stored) <= self.threshold:
                near.append(cid)
        return near

    def items(self) -> list[tuple[str, int]]:
        """导出 [(record_id, fingerprint)]，供持久化/重启恢复。"""
        return [(rid, fp) for rid, fp in self._fingerprints.items()]

    @classmethod
    def from_items(
        cls,
        items: Iterable[tuple[str, int]],
        threshold: int | None = None,
        block_count: int = 4,
    ) -> "SimHashIndex":
        """从持久化条目恢复索引。"""
        index = cls(threshold=threshold, block_count=block_count)
        for rid, fp in items:
            index.add(str(rid), int(fp))
        return index
