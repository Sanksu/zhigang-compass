# 003 · dict-guard 审批端点非原子：图谱已删而数据库不知情

> 日期：2026-08-24 · 影响面：`POST /api/v1/admin/dict-guard/proposals/{id}/review` 的 approve 路径
> 类别：数据一致性 / 事务边界

## 经过

08-24 图谱治理会话批量审批 79 条积压提案。执行脚本在 api 容器内以 `create_access_token("admin", "admin")` 签发临时令牌走真实审批端点，**首轮 74 条全部返回 500**（"服务器内部错误"）。

排查发现首轮并非没有效果——图谱里 Skill 4441→4399、Course 1491→1472 已实际减少；但 `dict_proposals.status` 全部仍是 pending。第二轮修正 token 后重跑，全部成功，但所有 remove_node/remove_edge 的 `removed_units` 都记为 0（节点在首轮已被删）。

## 根因

`backend/app/api/v1/admin_routes/dict_guard.py` 的 `review_proposal` 执行顺序：

1. 动态过滤层变更（`dyn.add_entry`，写 JSON 文件）
2. **Neo4j 删除**（`_cleanup_skill_nodes` / `_cleanup_by_proposal`，经 driver 直连）
3. PG 写提案状态 + DictChangeLog + AuditLog → `db.commit()`

三步**不在一个事务里**：第 3 步因 `AuditLog.user_id` 是 UUID 列、收到字符串 `'admin'` 而 asyncpg 校验失败时，PG 回滚，但第 1、2 步的副作用已经落盘且不可回滚。于是产生「图谱已删、词表已屏蔽、数据库却认为什么都没发生」的半执行态。

第二轮重跑之所以"成功"，只是因为目标节点已不存在：删除动作幂等地删了个寂寞（removed=0），状态与审计这才补记上账。

## 修复建议（待 PR）

- 前置校验：进入 approve 分支前先校验 operator 为合法 UUID（以及任何会在 commit 路径抛错的字段）
- 顺序调整：把 Neo4j 删除移到 `await db.commit()` 成功之后；commit 失败时图谱零副作用
- 更进一步：将 removed 统计改为先查后删（先 count 再 delete），或在删除前记录快照到 reports/（auto 路径已有此约定，manual 路径没有）

## 规则沉淀

1. **容器内签发运维令牌，`sub` 必须用 `users.id`（UUID），不能用用户名**——RBAC 只看 role，但审计写库看 UUID。
2. **跨存储（PG + Neo4j + 本地 JSON）的写路径没有原子性可言**，设计端点时必须把不可回滚的副作用放在最后一步，并让前面所有步骤尽量"可失败前置校验"。
3. 批量运维操作前先对关键表/图计数留底，事后对账才能像本次一样从 `-42/-19/-35` 的 delta 反推出半执行真相。
