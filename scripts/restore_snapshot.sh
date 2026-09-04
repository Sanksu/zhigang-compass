#!/usr/bin/env bash
# 一键初始化 + 数据快照导入：拉取/解压源码后零手工步骤，构建并导入 PG + Neo4j 全量快照。
# 用法: bash scripts/restore_snapshot.sh [快照目录]
#   快照目录（可选）: 含 pg.dump + neo4j.dump 的目录，缺省 <仓库>/snapshots；
#   交付提交包用户直接传包内目录，如 bash scripts/restore_snapshot.sh ../6-测试数据/数据快照
# 行为: 前置检查 → 引导 .env/JWT 密钥/dict-guard → 构建 → 起库 → 导入 → 全栈 → 健康检查。
# 可用性: bash + docker compose v2 + curl + openssl（Git Bash/macOS/Linux 均自带）。
set -euo pipefail

SNAP_ARG="${1:-}"

# 定位仓库根（从脚本目录向上找 docker-compose.yml；提交包内副本不在源码树，明确报错）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR"
while [ "$ROOT" != "/" ] && [ ! -f "$ROOT/docker-compose.yml" ]; do
  ROOT="$(dirname "$ROOT")"
done
[ -f "$ROOT/docker-compose.yml" ] || {
  echo "错误：未从脚本位置向上找到 docker-compose.yml。请使用源码树内的 scripts/restore_snapshot.sh。"
  exit 1
}

# [1/6] 前置检查：快照目录按调用者当前目录解析（支持 ../ 相对路径），再绝对化
if [ -n "$SNAP_ARG" ]; then
  [ -d "$SNAP_ARG" ] || { echo "错误：快照目录不存在：$SNAP_ARG"; exit 1; }
  SNAP_DIR="$(cd "$SNAP_ARG" && pwd)"
else
  SNAP_DIR="$ROOT/snapshots"
fi
[ -f "$SNAP_DIR/pg.dump" ] || {
  echo "错误：$SNAP_DIR/pg.dump 不存在。git 用户先执行 git lfs pull；提交包用户传 6-测试数据/数据快照 目录作为参数。"
  exit 1
}
[ -f "$SNAP_DIR/neo4j.dump" ] || { echo "错误：$SNAP_DIR/neo4j.dump 不存在。"; exit 1; }

echo "[1/6] 前置就绪：仓库 $ROOT"
echo "      快照目录 $SNAP_DIR"

# [2/6] 配置引导（必须在 compose build/up 之前）：
#   - backend/.env 是 compose env_file，缺文件 compose 直接报错；
#   - JWT 密钥缺失时 api 生产 fail-fast 拒绝启动；
#   - skill_filters_dynamic.json 缺失时 Docker 会把挂载点建成同名目录。
ENV_FILE="$ROOT/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/backend/.env.example" "$ENV_FILE"
  ADMIN_PASSWORD="$(openssl rand -hex 8)"
  sed -i.bak \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$ADMIN_PASSWORD|" \
    "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  echo "[2/6] 已生成 backend/.env（SECRET_KEY 随机 32 字节；管理员初始密码见下，请保存）"
  echo "      ADMIN_PASSWORD=$ADMIN_PASSWORD"
else
  echo "[2/6] backend/.env 已存在，跳过引导"
fi
KEYS_DIR="$ROOT/backend/keys"
if [ ! -f "$KEYS_DIR/private.pem" ] || [ ! -f "$KEYS_DIR/public.pem" ]; then
  mkdir -p "$KEYS_DIR"
  openssl genrsa -out "$KEYS_DIR/private.pem" 2048 2>/dev/null
  openssl rsa -in "$KEYS_DIR/private.pem" -pubout -out "$KEYS_DIR/public.pem" 2>/dev/null
  echo "      已生成 JWT 密钥对 backend/keys/{private,public}.pem"
fi
DICT_FILE="$ROOT/backend/configs/skill_filters_dynamic.json"
[ -f "$DICT_FILE" ] || printf '{\n  "version": 0,\n  "blocked": [],\n  "protected": []\n}\n' > "$DICT_FILE"

cd "$ROOT"
NEO4J_VOLUME="${NEO4J_VOLUME:-zhigang-compass_neo4j_data}"

echo "[3/6] 构建镜像（api/worker，有缓存时较快）..."
docker compose build

echo "[4/6] 启动 postgres/redis 并等待就绪..."
docker compose up -d postgres redis
for i in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U zhigang -d zhigang >/dev/null 2>&1; then
    break
  fi
  [ "$i" -eq 60 ] && { echo "错误：postgres 60×2s 内未就绪，请检查 docker compose logs postgres"; exit 1; }
  sleep 2
done

echo "[5/6] 导入快照：停 api/worker/neo4j → pg_restore → neo4j-admin load ..."
docker compose stop api worker neo4j >/dev/null 2>&1 || true
docker cp "$SNAP_DIR/pg.dump" zhigang-postgres:/tmp/pg.dump
# Git Bash(MSYS) 会把 / 开头的参数转换成宿主 Windows 路径：容器内路径需禁用转换，-v 宿主路径需转 C:/ 形式
docker_native() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) MSYS_NO_PATHCONV=1 "$@" ;;
    *) "$@" ;;
  esac
}
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SNAP_DIR_MOUNT="$(cygpath -m "$SNAP_DIR")" ;; *) SNAP_DIR_MOUNT="$SNAP_DIR" ;; esac
docker_native docker compose exec -T postgres pg_restore --clean --if-exists -U zhigang -d zhigang /tmp/pg.dump
docker_native docker compose exec -T postgres rm -f /tmp/pg.dump
docker_native docker run --rm \
  -v "$NEO4J_VOLUME":/var/lib/neo4j/data \
  -v "$SNAP_DIR_MOUNT":/dump:ro \
  --entrypoint "" neo4j:5 \
  neo4j-admin database load --from-path=/dump --overwrite-destination=true neo4j

echo "[6/6] 启动全栈并等待 /health（api 加载 SBERT 模型，最长约 4 分钟）..."
docker compose up -d
for i in $(seq 1 120); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "完成：全栈已就绪（http://localhost:8000），数据与图谱均为快照全量。"
    echo "  - 前端 Web UI（可选）：cd frontend && pnpm install && pnpm build，产物经挂载即时生效，无需重跑本脚本"
    echo "  - LLM 增强需配置 backend/configs/llm_providers.yaml（未配置时自动降级规则抽取，快照导入与健康检查不依赖它）"
    exit 0
  fi
  sleep 2
done
echo "错误：/health 120×2s 内未通过。排查：docker compose logs api --tail 50"
echo "  生产 fail-fast 四条件：SECRET_KEY≠change-me、ADMIN_PASSWORD≠admin123、DEBUG=false、CORS_ORIGINS 无 *"
echo "  首次部署另见 DEPLOY.md §7.1。"
exit 1
