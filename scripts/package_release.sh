#!/usr/bin/env bash
# 源码打包脚本（DO-M5-04：M5 源码打包提交）
#
# 产出：zhigang-compass-{version}.tar.gz（排除 .git/.venv/node_modules/dist 等构建产物）
# 用法：
#   bash scripts/package_release.sh [版本号]     # 默认取 git describe 或最新 tag
#   bash scripts/package_release.sh 1.0.0        # 指定版本
#
# 打包内容：源码 + 配置模板 + 文档 + 部署文件（可 30 分钟部署的全量源码包）

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-$(git describe --tags --always 2>/dev/null || echo snapshot)}"
OUT="zhigang-compass-${VERSION}.tar.gz"
STAGE=".release_stage"

echo "==> 打包版本: ${VERSION}"

# 清理历史产物
rm -rf "${STAGE}" "${OUT}"

# 暂存目录复制（保留符号链接）
mkdir -p "${STAGE}"
git archive HEAD | tar -x -C "${STAGE}"
# git archive 不包含未跟踪文件（如 .env.example 之外的本地配置）——补充关键未跟踪物
for extra in docs/m5 docs/perf_baseline_20260815.md CHANGELOG.md glossary.md; do
    if [ -e "${extra}" ] && ! [ -e "${STAGE}/${extra}" ]; then
        mkdir -p "${STAGE}/$(dirname "${extra}")"
        cp -r "${extra}" "${STAGE}/${extra}"
    fi
done

# 排除构建产物与敏感文件（双保险：archive 已排除，这里处理补充文件）
find "${STAGE}" -type d \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_tmp -o -name dist \) -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -name "*.pyc" -delete 2>/dev/null || true
rm -f "${STAGE}/backend/.env" "${STAGE}/frontend/.env" 2>/dev/null || true

# 打包
tar -czf "${OUT}" -C "${STAGE}" .
rm -rf "${STAGE}"

echo "==> 完成: ${OUT}"
echo "    大小: $(du -h "${OUT}" | cut -f1)"
echo "    校验: sha256sum ${OUT}"
