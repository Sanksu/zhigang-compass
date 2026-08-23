"""图谱查询仓储服务（graph.py 查询/仓储拆分产物）。

分层：
- visibility.py    ：岗位可见性纯函数（状态集合/角色判定/状态过滤子句）
- queries.py       ：会话级 Neo4j 查询函数（接收 session，不含驱动/会话管理）
- repository.py    ：仓储层——接收驱动 → 开 session → 委托 queries.py
"""
