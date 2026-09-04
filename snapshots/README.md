# 数据快照（snapshots/）

本地部署可直接导入的完整系统快照（PostgreSQL + Neo4j），用于**跳过冷启动**直接获得可用数据（图谱 118 岗位节点 / JD 11115 条 / 演化快照 27 期等）。

> 由本机运行环境导出（2026-09-04 更新），二进制经 Git LFS 托管（`pg.dump` > GitHub 100MB 单文件限制）。

## 文件

| 文件 | 格式 | 说明 | 大小 |
|------|------|------|------|
| `pg.dump` | PostgreSQL `pg_dump -Fc`（custom） | 全量 PG：JD 原始数据 / 抽取 / 候选池 / 图谱版本快照 / 用户等 | 123MB |
| `neo4j.dump` | `neo4j-admin database dump`（DZV1） | 图谱：Position/Skill/Course/Evidence 节点与关系 | 42MB |

## 导入

一键导入（自动构建镜像、引导 `.env`/JWT 密钥/dict-guard 空层、导入、健康检查；Linux/226 部署机）：

```bash
bash scripts/restore_snapshot.sh                  # 缺省读本目录（snapshots/）
bash scripts/restore_snapshot.sh <其他快照目录>   # 交付提交包用户直接传 6-测试数据/数据快照 目录，免拷贝
```

或手工步骤（Win/Linux 通用）：

```bash
# 1. 停依赖服务（Neo4j 离线 load 前必须停止）
docker compose stop api worker neo4j

# 2. PostgreSQL 恢复
docker cp snapshots/pg.dump zhigang-postgres:/tmp/pg.dump
docker compose exec -T postgres pg_restore --clean --if-exists -U zhigang -d zhigang /tmp/pg.dump
docker compose exec -T postgres rm -f /tmp/pg.dump

# 3. Neo4j 恢复（数据卷挂到镜像默认路径，离线 load）
docker run --rm \
  -v zhigang-compass_neo4j_data:/var/lib/neo4j/data \
  -v "$(pwd)/snapshots":/dump \
  --entrypoint "" neo4j:5 \
  neo4j-admin database load --from-path=/dump --overwrite-destination=true neo4j

# 4. 启动
docker compose up -d
```

## 重新导出（更新快照）

```bash
# PG（在线导出，MVCC 一致性快照）
docker exec zhigang-postgres pg_dump -U zhigang -d zhigang -Fc -f /tmp/pg.dump
docker cp zhigang-postgres:/tmp/pg.dump snapshots/pg.dump
docker exec zhigang-postgres rm -f /tmp/pg.dump

# Neo4j（需停库，保证一致）
docker compose stop api worker neo4j
docker run --name zhigang-neo4j-dump \
  -v zhigang-compass_neo4j_data:/var/lib/neo4j/data \
  --entrypoint "" neo4j:5 \
  neo4j-admin database dump --to-path=/tmp neo4j
docker cp zhigang-neo4j-dump:/tmp/neo4j.dump snapshots/neo4j.dump
docker rm -f zhigang-neo4j-dump
docker compose start
```

> 注意：`neo4j-admin database dump` 的 `<database>` 是**位置参数**（非 `--database=`）。导出前先 `git lfs pull` 确保本地有二进制内容。
