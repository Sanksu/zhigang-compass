#!/usr/bin/env bash
# 从 snapshots/ 恢复完整数据快照（PostgreSQL + Neo4j）——跳过冷启动直接获得可用数据。
# 用法: bash scripts/restore_snapshot.sh
# 前置: docker compose 5 服务已构建；snapshots/pg.dump + snapshots/neo4j.dump 已就位（git lfs pull）。
set -euo pipefail

cd "$(dirname "$0")/.."
SNAP_DIR="$(pwd)/snapshots"
[ -f "$SNAP_DIR/pg.dump" ] || { echo "缺少 $SNAP_DIR/pg.dump（先 git lfs pull）"; exit 1; }
[ -f "$SNAP_DIR/neo4j.dump" ] || { echo "缺少 $SNAP_DIR/neo4j.dump（先 git lfs pull）"; exit 1; }

echo "[1/4] 停止 api/worker/neo4j（Neo4j 离线 load 前置）..."
docker compose stop api worker neo4j

echo "[2/4] 恢复 PostgreSQL（pg_restore --clean custom dump）..."
docker cp "$SNAP_DIR/pg.dump" zhigang-postgres:/tmp/pg.dump
docker compose exec -T postgres pg_restore --clean --if-exists -U zhigang -d zhigang /tmp/pg.dump
docker compose exec -T postgres rm -f /tmp/pg.dump

echo "[3/4] 恢复 Neo4j（neo4j-admin database load，数据卷挂镜像默认路径）..."
docker run --rm \
  -v zhigang-compass_neo4j_data:/var/lib/neo4j/data \
  -v "$SNAP_DIR":/dump \
  --entrypoint "" neo4j:5 \
  neo4j-admin database load --from-path=/dump --database=neo4j --overwrite-destination=true

echo "[4/4] 启动全部服务..."
docker compose up -d

echo "完成：数据已从 snapshots/ 导入，api/worker 启动后走 alembic（版本已含于 dump，无额外迁移）。"
