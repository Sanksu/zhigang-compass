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
# git archive 不包含未跟踪文件（如 docs/m5 新产出、本地新增物料）——补充关键未跟踪物。
# 注意：archive 已含同名目录（部分已跟踪文件）时不能跳过——`-e` 判断会漏掉
# 该目录下的未跟踪新文件（08-15 审查：docs/m5 已入库后 extra 循环成死代码），
# 必须合并复制（目录内容级拷贝，不覆盖 archive 已有内容）。
for extra in docs/m5 docs/perf_baseline_20260815.md CHANGELOG.md glossary.md; do
    if [ -e "${extra}" ]; then
        mkdir -p "${STAGE}/$(dirname "${extra}")"
        if [ -d "${extra}" ]; then
            cp -r "${extra}/." "${STAGE}/${extra}/"
        else
            cp -r "${extra}" "${STAGE}/${extra}"
        fi
    fi
done

# 排除构建产物与敏感文件（双保险：archive 已排除，这里处理补充文件）
find "${STAGE}" -type d \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_tmp -o -name dist -o -name .pnpm-store -o -name .trae-html-share-packages -o -name .uploads \) -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}" -name "*.pyc" -delete 2>/dev/null || true
rm -f "${STAGE}/backend/.env" "${STAGE}/frontend/.env" 2>/dev/null || true

# 排除大型媒体交付物（PPT/视频/音频——作为独立交付物提交，不纳入源码包）
rm -rf "${STAGE}/docs/m5/video_audio" "${STAGE}/docs/m5/video_slides" 2>/dev/null || true
find "${STAGE}/docs/m5" \( -name "*.mp4" -o -name "*.pptx" -o -name "*.wav" -o -name "*.mp3" \) -delete 2>/dev/null || true
# 排除数据快照 dump（#786 Git LFS 大文件，173MB——数据快照作为独立交付物提交，
# 恢复走 scripts/restore_snapshot.sh + 云盘独立附件，不纳入源码包）
rm -f "${STAGE}/snapshots/pg.dump" "${STAGE}/snapshots/neo4j.dump" 2>/dev/null || true
# 排除数据快照 dump（#786 Git LFS 大文件，173MB——数据快照作为独立交付物提交，
# 恢复走 scripts/restore_snapshot.sh + 云盘独立附件，不纳入源码包）
rm -f "${STAGE}/snapshots/pg.dump" "${STAGE}/snapshots/neo4j.dump" 2>/dev/null || true

# 打包
tar -czf "${OUT}" -C "${STAGE}" .
rm -rf "${STAGE}"

echo "==> 完成: ${OUT}"
echo "    大小: $(du -h "${OUT}" | cut -f1)"
echo "    校验: sha256sum ${OUT}"
