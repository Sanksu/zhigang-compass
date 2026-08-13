# 盲审 round2 人工复核清单（2026-08-13）

## 背景

- 盲审 round1 仅 12 条；M5 收尾 JD 盲审 F1 ≥ 0.90 需扩充盲审集至 **30+ 条**
- round2 新增 **20 条**：来自 zhilian 采集快照（8-13 详情正文补抓后 322 条 >300 字），按岗位类型挑选与 round1 互补（前端/后端/大模型应用/数字后端/图像/机器人/SLAM/视觉/摄影测量/数据分析/Java 等），均满足 round1 门槛：可追溯 `source_url` + 非空真实详情正文
- `review_gold_*` 六字段为 JDExtractor（LLM）抽取**草稿**（`review_status=AI预标_待复核`），**未经人工定稿不得作为评测 gold**——盲审 gold 独立性由人工复核保证

## 复核步骤

1. 打开 `jd_manual_review_round2.xlsx`（sheet：`Round2盲标`）
2. 逐条对照 `detail_raw_text` 修订 `review_gold_*` 六字段（title / skills / bonus_skills / experience / education / core_duties）
3. 修订完 `review_status` 改「已复核」，`annotator` 填标注人
4. 定稿后跑评测（annotator 非空后 preflight 放行）

## 样本清单（20 条）

| sample_id | 招聘标题 | gold_title 草稿 | skills 数 | edu 草稿 | 正文长度 | 关注点 |
|---|---|---|---|---|---|---|
| r2_001 | 前端开发工程师 | 前端开发工程师 | 32 | — | 1311 | 草稿一致 |
| r2_002 | 后端开发工程师 | 后端开发工程师 | 9 | 本科 | 562 | 草稿一致 |
| r2_003 | 大模型算法工程师 | 大模型应用工程师 | 16 | — | 1237 | ⚠️ title 口径：LLM 应用 vs 算法 |
| r2_004 | 数字后端工程师 | 物理设计工程师 | 11 | 本科 | 1252 | ⚠️ title 口径：数字后端=芯片物理设计，语义相关 |
| r2_005 | 图像算法工程师 | 图像识别算法工程师 | 20 | 本科 | 945 | ⚠️ title 细分为图像识别 |
| r2_006 | java开发工程师 | 全栈开发工程师 | 21 | 本科 | 693 | ⚠️ 正文含前端职责时全栈成立 |
| r2_007 | 数据分析工程师 | 数据分析师 | 8 | — | 962 | ⚠️ title 口径：工程师 vs 师 |
| r2_008 | 大模型应用开发工程师 | 大模型应用开发工程师 | 23 | 本科 | 1331 | 草稿一致 |
| r2_009 | 大模型测试工程师 | 大模型测试工程师 | 7 | 本科 | 351 | 草稿一致（正文短） |
| r2_010 | 机器人算法工程师 | 机器人控制算法工程师 | 18 | 本科 | 476 | ⚠️ title 细分为控制 |
| r2_011 | slam算法工程师 | 机器人系统工程师 | 17 | 本科 | 1238 | ⚠️ title 口径偏泛，建议 SLAM 算法工程师 |
| r2_012 | 计算机视觉算法工程师 | 计算机视觉工程师 | 19 | 本科 | 475 | ⚠️ title 略泛 |
| r2_013 | 摄影测量高级算法工程师 | 机器视觉算法工程师 | 18 | 本科 | 473 | ⚠️ 子领域口径：摄影测量→机器视觉 |
| r2_014 | React前端开发工程师 | 前端开发工程师 | 7 | 本科 | 460 | ⚠️ 泛化：React 是否保留 |
| r2_015 | 高级前端工程师 | 前端开发工程师 | 7 | — | 350 | ⚠️ 泛化：级别是否保留 |
| r2_016 | 全栈开发工程师 | 全栈开发工程师 | 21 | 本科 | 837 | 草稿一致 |
| r2_017 | AI全栈工程师 | 客户端开发工程师 | 11 | — | 781 | ⚠️ 疑似正文确为客户端岗，须核对 |
| r2_018 | Python工程师 | 计算金融软件工程师 | 10 | 本科 | 2009 | ⚠️ 疑似量化金融岗，须核对 |
| r2_019 | java后端工程师 | Java 后端开发工程师 | 22 | 本科 | 541 | ⚠️ 大小写/空格规范 |
| r2_020 | 视觉算法工程师 | 机器视觉算法工程师 | 23 | 硕士 | 1392 | ⚠️ title 细分为机器视觉 |

## 定稿后评测命令

```bash
cd backend
uv run -- python tests/evaluate/run_manual_jd_eval.py \
  --xlsx data/golden_set/review/jd_manual_review_round2.xlsx \
  --sheet "Round2盲标" \
  --output-dir data/golden_set/review/evaluation_round2 --run
```

合并口径（round1 12 条 + round2 20 条 = 32 条）：两份报告合读，或后续把 round2 样本并入 round1 工作簿统一跑。

## 产物文件

| 文件 | 说明 |
|---|---|
| `jd_manual_review_round2.xlsx` | 盲标工作簿（AI 草稿 + 待复核） |
| `round2_candidates.jsonl` | 20 条候选源数据（可追溯 source_url/source_id） |
| `round2_drafts_cache.json` | 抽取草稿缓存（重建工作簿不重跑 LLM） |
| `tests/evaluate/build_blind_review_round2.py` | 生成脚本（选样→LLM 草稿→xlsx） |
| `evaluation_round2/manual_jd_eval_data_validation.json` | preflight 校验（20/20 通过，annotator 空为预期阻塞） |
