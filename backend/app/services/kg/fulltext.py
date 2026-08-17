"""Neo4j 全文查询（Lucene 语法）共享工具。

retrieval 与 grounding 两处曾各有一份逐字节相同的 _sanitize_fulltext，
收敛于此（08-17 精简审查）。查询前剔除 Lucene 特殊字符，避免语法异常。
"""

# Lucene 特殊字符：+ - && || ! ( ) { } [ ] ^ " ~ * ? : \ /
_LUCENE_SPECIAL = frozenset('+-&|!(){}[]^"~*?:\\/')


def sanitize_fulltext(q: str) -> str:
    """剔除 Neo4j 全文查询的 Lucene 特殊字符，空串视为无关键词命中。"""
    return "".join(ch for ch in q if ch not in _LUCENE_SPECIAL).strip()
