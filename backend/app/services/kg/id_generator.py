"""Neo4j 自增 ID 生成。

使用 Counter 节点实现原子自增，prefix 映射表：

| entity_type | prefix | 示例       |
|-------------|--------|------------|
| Position    | pos    | pos_0001   |
| Skill       | sk     | sk_0042    |
| Evidence    | ev     | ev_0123    |
| Course      | co     | co_0001    |
| Occupation  | oc     | oc_1639    |
| Certification| ce    | ce_0007    |
| Education   | ed     | ed_0001    |
| Tool        | tl     | tl_0012    |
| PositionEditLog | pl | pl_0001    |
"""

PREFIX_MAP = {
    "Position": "pos",
    "Skill": "sk",
    "Evidence": "ev",
    "Course": "co",
    "Occupation": "oc",
    "Certification": "ce",
    "Education": "ed",
    "Tool": "tl",
    "PositionEditLog": "pl",
}


def next_id(tx, entity_type: str) -> str:
    """原子地获取下一个 ID，格式 `{prefix}_{seq:04d}`。"""
    prefix = PREFIX_MAP.get(entity_type)
    if prefix is None:
        raise ValueError(f"未知实体类型: {entity_type}")

    result = tx.run(
        """\
MERGE (c:Counter {type: $entity_type})
SET c.value = coalesce(c.value, 0) + 1
RETURN c.value AS seq
""",
        entity_type=entity_type,
    )
    seq = result.single()["seq"]
    return f"{prefix}_{seq:04d}"
