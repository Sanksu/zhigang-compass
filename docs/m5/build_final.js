/* 智岗罗盘答辩 PPT 终稿构建脚本（DO-M5-01）
 * 视觉体系：深蓝主色（Navy）+ 产品信号绿点缀（Emerald）+ AI 生成概念图
 * 画布 13.33×7.5in；字体 微软雅黑 + 宋体（标题）
 * 09-01 口径同步：aligned F1 0.9629（词面豁免口径）/ 简历 0.988 /
 * 匹配 Acc 0.9714（BT v4，golden_set_match_v3 384 对含学历维度）/ 5 骨干职能域+通用域（08-31 治理）/ 三视图（08-29 收敛）
 * 09-03 口径更新（对齐口径速查，单一事实源见下方 CAL 常量）：JD aligned F1 0.9632 / 10 职能域 /
 * 09-04 实查口径：数据截至 graph_v20260903 快照（22570 节点/77065 边 · 118 岗位/7342 技能 · 先修链字典 242 · 累计入库 17250）
 */
const pptxgen = require("pptxgenjs");

const W = 13.33, H = 7.5, M = 0.5;
const CW = W - 2 * M; // content width

// ── 口径常量（单一事实源，与 docs/m5/口径速查_初审版_20260901 对齐）──
// 改数字只改此处，勿在页内硬编码——避免 0.9629 散落多处的旧口径误用。
const CAL = {
  JD_F1: "0.9632",            // JD 解析 aligned F1（08-28 正式验收三跑极差 0）
  JD_F1_NOTE: "110 条 gold · 目标 ≥0.90",
  RESUME_F1: "0.988",
  MATCH_ACC: "0.9714",        // BT v4，384 对含学历维度
  LEARN: "96.7%",
  P95: "430ms",
  DOMAINS: "10 职能域",
  DOMAINS_FULL: "5 大骨干 + 4 显式语义域（网络安全/AI应用与智能体/企业应用与系统/产品与项目管理）+ 通用兜底",
  SNAP: "graph_v20260903",
  SKILLS: "7,342",
  POSITIONS: "118",
  NODES_EDGES: "22570 节点/77065 边",
  INGESTED: "17,250",
};

// 调色板
const BG = "FFFFFF";
const BG_DARK = "0F1B2D";
const NAVY = "1B3A5C";
const NAVY_MID = "2E5F8A";
const TINT = "EDF2F8";
const TINT2 = "F7FAFD";
const ACCENT = "10B981";
const ACCENT_D = "0B815F";
const INK = "17263B";
const MUTED = "5B6B7E";
const WARN = "D97706";
const FONT_HEAD = "SimSun";     // 标题宋体（衬线）
const FONT_BODY = "Microsoft YaHei"; // 正文雅黑
const IMG = "images/";

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.author = "智岗罗盘团队";
p.title = "智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统";

const shadow = () => ({ type: "outer", color: "1B3A5C", blur: 7, offset: 2, angle: 45, opacity: 0.18 });
const bu = () => ({ code: "25B8", indent: 12 });

let pageNo = 0;
function base(dark = false) {
  pageNo += 1;
  const s = p.addSlide();
  s.background = { color: dark ? BG_DARK : BG };
  if (pageNo > 1 && pageNo < 19) {
    s.addText(`${pageNo} / 19`, { x: W - 1.3, y: 0.38, w: 0.9, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: "9AA9BA", align: "right", margin: 0 });
  }
  return s;
}
function title(s, kicker, txt, dark = false) {
  s.addText(kicker, { x: M, y: 0.34, w: 9, h: 0.28, fontSize: 12, fontFace: FONT_BODY, color: ACCENT_D, bold: true, charSpacing: 2, margin: 0 });
  s.addText(txt, { x: M, y: 0.62, w: CW, h: 0.62, fontSize: 29, fontFace: FONT_HEAD, color: dark ? "FFFFFF" : NAVY, bold: true, margin: 0 });
}
function shot(s, file, x, y, w, opts = {}) {
  const h = opts.h || w * (1080 / 1920);
  s.addShape(p.shapes.RECTANGLE, { x: x - 0.06, y: y - 0.06, w: w + 0.12, h: h + 0.12, fill: { color: "FFFFFF" }, line: { color: "D7E0EA", width: 0.75 }, shadow: shadow() });
  s.addImage({ path: IMG + file, x, y, w, h, sizing: { type: "cover", w, h } });
  return h;
}
function bullets(s, items, x, y, w, h, opts = {}) {
  const arr = items.map((t, i) => ({ text: t, options: { bullet: bu(), breakLine: i < items.length - 1, color: opts.color || INK } }));
  s.addText(arr, { x, y, w, h, fontSize: opts.fontSize || 14.5, fontFace: FONT_BODY, paraSpaceAfter: opts.gap || 9, margin: 0, valign: "top" });
}
function stat(s, x, y, w, num, label, opts = {}) {
  s.addText(num, { x, y, w, h: opts.numH || 0.72, fontSize: opts.numSize || 40, fontFace: FONT_HEAD, bold: true, color: opts.color || NAVY, align: "center", margin: 0 });
  s.addText(label, { x, y: y + (opts.numH || 0.72), w, h: opts.labelH || 0.55, fontSize: 12.5, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0, valign: "top" });
}
// 4:3 图片卡（保持比例）
function shot43(s, file, x, y, w) {
  const h = w * 0.75;
  s.addShape(p.shapes.RECTANGLE, { x: x - 0.06, y: y - 0.06, w: w + 0.12, h: h + 0.12, fill: { color: "FFFFFF" }, line: { color: "D7E0EA", width: 0.75 }, shadow: shadow() });
  s.addImage({ path: IMG + file, x, y, w, h, sizing: { type: "cover", w, h } });
  return h;
}

/* ========== 1 封面（深底 + 图谱全景） ========== */
(() => {
  const s = base(true);
  s.addText("科大讯飞挑战杯 · 揭榜挂帅    项目编号 XH-202621", { x: M, y: 1.5, w: CW, h: 0.4, fontSize: 14, fontFace: FONT_BODY, color: "8FD4BC", charSpacing: 2, margin: 0 });
  s.addText("智岗罗盘", { x: M, y: 2.35, w: CW, h: 1.25, fontSize: 66, fontFace: FONT_HEAD, bold: true, color: "FFFFFF", charSpacing: 8, margin: 0 });
  s.addText("多源异构驱动的岗位能力动态演化与人岗匹配系统", { x: M, y: 3.8, w: CW, h: 0.55, fontSize: 22, fontFace: FONT_BODY, color: "D8E4F0", margin: 0 });
  s.addShape(p.shapes.LINE, { x: M, y: 4.7, w: 3.2, h: 0, line: { color: ACCENT, width: 2.5 } });
  s.addText("项目周期 2026.07.13 — 09.05    ·    团队 6 人（前端 / 后端 / 算法 / 数据 / 测试 / 文档）", { x: M, y: 5.0, w: CW, h: 0.4, fontSize: 13, fontFace: FONT_BODY, color: "9FB4CC", margin: 0 });
})();

/* ========== 2 背景与痛点 ========== */
(() => {
  const s = base();
  title(s, "BACKGROUND", "项目背景与行业痛点");
  const rows = [
    ["01", "技术迭代 远快于 人才培养", "AI / 大模型每季度出现新方向，本科培养周期 4 年——技能供给天然滞后于岗位需求"],
    ["02", "结构性供需错配", "企业「招不到合适的人」，求职者「不知道学什么」——缺一个可量化的能力坐标参照系"],
    ["03", "招聘 JD 三大顽疾", "时滞（沿用 3 年前要求） · 抄袭（同质化严重） · 通胀（要求虚高）——JD 信号本身失真"],
    ["04", "传统匹配停留在关键词", "无技能级差距诊断、无个性化学习路径——「匹不配得上」说不清，「差什么」更说不清"],
  ];
  rows.forEach((r, i) => {
    const y = 1.62 + i * 1.32;
    s.addText(r[0], { x: M, y: y - 0.06, w: 1.15, h: 1.0, fontSize: 40, fontFace: FONT_HEAD, bold: true, color: i === 2 ? ACCENT_D : "C3D2E2", margin: 0 });
    s.addText(r[1], { x: 1.8, y, w: 10.9, h: 0.4, fontSize: 17, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0 });
    s.addText(r[2], { x: 1.8, y: y + 0.44, w: 10.9, h: 0.42, fontSize: 13.5, fontFace: FONT_BODY, color: MUTED, margin: 0 });
    if (i < 3) s.addShape(p.shapes.LINE, { x: 1.8, y: y + 1.06, w: 10.5, h: 0, line: { color: "E3EAF2", width: 0.75 } });
  });
})();

/* ========== 3 核心创新点（3+1） ========== */
(() => {
  const s = base();
  title(s, "INNOVATION", "核心创新点（3 + 1）");
  const cards = [
    ["动态演化", "从「静态画像」到「动态感知」——岗位能力图谱随技术趋势自我进化，新兴/衰退信号驱动更新", "核心竞争力"],
    ["多源交叉验证", "JD × 课程 × 论文/社区 × 国家职业分类四源融合，每日计算一致性并在看板展示", "数据可信"],
    ["技能级人岗匹配", "三维加权评分 + 差距三态诊断 + 个性化学习路径——回答「差什么、学什么、怎么学」", "落地闭环"],
    ["幻觉防控三道防线", "Pydantic Schema 强校验 → 词典/白名单过滤 → 证据链可追溯，LLM 输出全量可回放", "工程护栏"],
  ];
  cards.forEach((c, i) => {
    const x = M + (i % 2) * 6.22, y = 1.7 + Math.floor(i / 2) * 2.62;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: 5.9, h: 2.32, fill: { color: i === 0 ? TINT : TINT2 }, line: { color: "DCE6F0", width: 0.75 }, rectRadius: 0.08 });
    s.addText(String(i + 1).padStart(2, "0"), { x: x + 0.28, y: y + 0.22, w: 1.0, h: 0.7, fontSize: 34, fontFace: FONT_HEAD, bold: true, color: i === 0 ? ACCENT_D : "B9CBDD", margin: 0 });
    s.addText(c[0], { x: x + 1.25, y: y + 0.3, w: 3.4, h: 0.45, fontSize: 18, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0 });
    s.addText(c[2], { x: x + 4.35, y: y + 0.36, w: 1.35, h: 0.32, fontSize: 11, fontFace: FONT_BODY, color: ACCENT_D, bold: true, align: "right", margin: 0 });
    s.addText(c[1], { x: x + 0.32, y: y + 1.02, w: 5.28, h: 1.1, fontSize: 13, fontFace: FONT_BODY, color: INK, margin: 0, valign: "top" });
  });
})();

/* ========== 4 系统总体架构（原生图） ========== */
(() => {
  const s = base();
  title(s, "ARCHITECTURE", "系统总体架构 · 五服务容器化");
  const layer = (y, h, name, chips, tint) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: CW, h, fill: { color: tint }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.06 });
    s.addText(name, { x: M + 0.25, y: y + h / 2 - 0.3, w: 1.5, h: 0.6, fontSize: 15, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0, valign: "middle" });
    chips.forEach((c, i) => {
      const cw = 1.95, cx = 2.35 + i * (cw + 0.28);
      s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: cx, y: y + h / 2 - 0.34, w: cw, h: 0.68, fill: { color: "FFFFFF" }, line: { color: "C9D7E5", width: 0.75 }, rectRadius: 0.06 });
      s.addText(c, { x: cx, y: y + h / 2 - 0.34, w: cw, h: 0.68, fontSize: 12.5, fontFace: FONT_BODY, color: INK, align: "center", valign: "middle", margin: 0 });
    });
  };
  layer(1.55, 1.28, "应用层", ["FastAPI 统一 API", "React 19 前端", "同端口静态托管", "CORS/CSP/限流"], TINT2);
  s.addShape(p.shapes.LINE, { x: W / 2, y: 2.9, w: 0, h: 0.3, line: { color: NAVY_MID, width: 1.75, endArrowType: "triangle" } });
  layer(3.24, 1.28, "图谱/算法层", ["Neo4j 5 图谱+cjk", "pgvector 向量", "匹配/演化引擎", "LLM 抽取管线"], TINT);
  s.addShape(p.shapes.LINE, { x: W / 2, y: 4.59, w: 0, h: 0.3, line: { color: NAVY_MID, width: 1.75, endArrowType: "triangle" } });
  layer(4.93, 1.28, "采集层", ["13 源三级爬虫", "代理池三梯队", "SimHash 去重", "质量评分管线"], TINT2);
  // 五服务容器条
  s.addText("五服务容器化部署（docker compose 一键启动，全服务健康检查）", { x: M, y: 6.5, w: 6.5, h: 0.32, fontSize: 12.5, fontFace: FONT_BODY, color: MUTED, margin: 0 });
  ["api", "postgres + pgvector", "redis 7", "neo4j 5", "worker (ARQ)"].forEach((c, i) => {
    const cw = 2.28, cx = M + i * (cw + 0.14);
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: cx, y: 6.86, w: cw, h: 0.44, fill: { color: NAVY }, rectRadius: 0.06 });
    s.addText(c, { x: cx, y: 6.86, w: cw, h: 0.44, fontSize: 12, fontFace: FONT_BODY, color: "FFFFFF", align: "center", valign: "middle", margin: 0 });
  });
})();

/* ========== 5 全流程链路（新增） ========== */
(() => {
  const s = base();
  title(s, "END-TO-END PIPELINE", "全流程链路 · 从多源采集到用户视图");
  const steps = [
    ["01", "多源采集", "13 源 A/B/C 分级 + 信号层\n代理池三梯队 · robots 合规"],
    ["02", "清洗去重", "质量评分 · SimHash 语义去重\n时效加权 · 长度过滤"],
    ["03", "原始库落库", "PostgreSQL 四大 raw 表\n累计入库 17,250 条"],
    ["04", "LLM 结构化抽取", "三道防线防幻觉\n六域决策信封全量落库可回放"],
    ["05", "图谱构建", "Neo4j 22,570 节点 / 77,065 边\n快照版本化 graph_v*"],
    ["06", "匹配 / 演化引擎", "技能级三维评分\n双信号岗位生命周期检测"],
    ["07", "学习路径", "先修链拓扑排序 · 242 技能字典\n课程 LEARNABLE_VIA · 甘特学时"],
    ["08", "前端三视图", "全景 / 技术栈 / 岗位画像\nReact 19 + ECharts"],
  ];
  const w = 2.95, gap = 0.14, rh = 1.9, rowY = [1.75, 4.15];
  steps.forEach((st, i) => {
    const x = M + (i % 4) * (w + gap), y = rowY[Math.floor(i / 4)];
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w, h: rh, fill: { color: i === 4 ? TINT : TINT2 }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.08 });
    s.addText(st[0], { x: x + 0.22, y: y + 0.16, w: 0.9, h: 0.5, fontSize: 22, fontFace: FONT_HEAD, bold: true, color: i === 4 ? ACCENT_D : "B9CBDD", margin: 0 });
    s.addText(st[1], { x: x + 0.22, y: y + 0.66, w: w - 0.44, h: 0.36, fontSize: 14.5, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0 });
    s.addText(st[2], { x: x + 0.22, y: y + 1.06, w: w - 0.44, h: 0.72, fontSize: 11, fontFace: FONT_BODY, color: MUTED, margin: 0, valign: "top" });
    if (i % 4 < 3) s.addText("→", { x: x + w - 0.03, y: y + rh / 2 - 0.25, w: 0.2, h: 0.5, fontSize: 15, fontFace: FONT_BODY, bold: true, color: ACCENT_D, align: "center", valign: "middle", margin: 0 });
  });
  s.addText("↓", { x: W - M - 0.5, y: 3.6, w: 0.5, h: 0.5, fontSize: 18, fontFace: FONT_BODY, bold: true, color: ACCENT_D, align: "center", valign: "middle", margin: 0 });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 6.3, w: CW, h: 0.62, fill: { color: TINT }, line: { color: "C9D7E5", width: 0.75 }, rectRadius: 0.06 });
  s.addText("数据流：17,250 条原始数据 → LLM 结构化抽取（每条断言可回指原始 JD）→ 图谱 22,570 节点/77,065 边 → 四源每日一致性校验 → 用户视图", { x: M + 0.25, y: 6.3, w: CW - 0.5, h: 0.62, fontSize: 12.5, fontFace: FONT_BODY, color: INK, valign: "middle", margin: 0 });
})();

/* ========== 6 数据采集与治理 ========== */
(() => {
  const s = base();
  title(s, "DATA COLLECTION", "数据采集与治理 · 招聘源 A/B/C 分级 + 信号层");
  // 左侧：分级图示
  const tiers = [
    ["A 级（核心招聘）", "智联招聘 · BOSS直聘（暂停 · 可逆恢复）· Monster（08.06 停采）", ACCENT_D],
    ["B 级（国际招聘）", "Indeed · Glassdoor", NAVY],
    ["C 级（社区/公开页）", "脉脉（夜间实验）· LinkedIn 公开页", NAVY_MID],
    ["信号层（课程/权威/趋势）", "MOOC · Coursera · edX · arXiv · GitHub/Stack Overflow · 国家职业分类 CSV", MUTED],
  ];
  tiers.forEach((t, i) => {
    const y = 1.62 + i * 0.95;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 5.9, h: 0.8, fill: { color: i === 0 ? TINT : TINT2 }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.06 });
    s.addText(t[0], { x: M + 0.25, y: y + 0.08, w: 3.0, h: 0.32, fontSize: 12.5, fontFace: FONT_BODY, bold: true, color: t[2], margin: 0 });
    s.addText(t[1], { x: M + 0.25, y: y + 0.42, w: 5.4, h: 0.32, fontSize: 11, fontFace: FONT_BODY, color: MUTED, margin: 0 });
  });
  bullets(s, [
    "国内直连 + 国际代理池三梯队：随机轮换 → 失败剔除 → 直连兜底",
    "清洗管线：长度过滤 → 质量评分 → SimHash 语义去重 → 时效加权",
    "合规红线：robots.txt 遵循 + 请求间隔 + 官方 API 条款背书",
    "累计入库 " + CAL.INGESTED + " 条（JD 11,115 · 课程 1,754 · 论文 2,429 · 社区 1,952），09-04 实查全链路可审计",
  ], M, 5.4, 5.9, 1.8, { fontSize: 13 });
  // 右侧：架构流程
  shot(s, "image-pipeline_16x9.jpg", 6.7, 1.72, 6.1);
  s.addText("▲ 数据治理管线：采集 → 清洗 → 去重 → 质量评分", { x: 6.7, y: 5.28, w: 6.1, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
})();

/* ========== 6 知识图谱构建 ========== */
(() => {
  const s = base();
  title(s, "KNOWLEDGE GRAPH", "知识图谱构建（Neo4j 5）");
  shot(s, "image-graph_16x9.jpg", 6.7, 1.72, 6.1);
  s.addText("▲ 力导向图谱：岗位 / 技能 / 课程 / 证据 多实体异构网络", { x: 6.7, y: 5.28, w: 6.1, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  bullets(s, [
    "实体：Position / Skill / Course / Evidence / Occupation（对接国家职业分类 3,240）",
    "关系：REQUIRES 11,714（权重+必要性）· LEARNABLE_VIA 9,741 · PREREQUISITE_OF 288 · EVIDENCED_BY 47,911 证据链",
    "岗位名归一化：技术栈细分保留（如 React 前端工程师）+ 失真兜底族技能路由",
    "cjk 全文索引内建于 Neo4j——中文检索零额外服务（替代 Elasticsearch）",
    CAL.DOMAINS + "（" + CAL.DOMAINS_FULL + "），归类依据逐岗可查",
  ], M, 1.85, 5.9, 4.6, { fontSize: 14 });
})();

/* ========== 7 LLM 抽取与幻觉防控 ========== */
(() => {
  const s = base();
  title(s, "LLM PIPELINE", "LLM 抽取与幻觉防控 · 三道防线");
  bullets(s, [
    "分层 Prompt（System / Task / Few-Shot）+ 多 Provider 重试链（OpenAI 兼容协议）",
    "六域决策信封全量落库：shadow / proposal / auto_applied / blocked 全程可追溯、可回放",
  ], M, 1.5, CW, 0.95, { fontSize: 14 });
  const steps = [
    ["第一道 · Schema 强校验", "Pydantic 结构化输出契约，非法输出即拒"],
    ["第二道 · 词典过滤", "白名单 / 停用词词典，拦截业务脏词与歧义词"],
    ["第三道 · 证据链追溯", "每条断言可回指原始 JD，支持回放审计"],
  ];
  steps.forEach((st, i) => {
    const x = M + i * 4.28;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 2.62, w: 3.86, h: 1.5, fill: { color: TINT }, line: { color: "C9D7E5", width: 0.75 }, rectRadius: 0.08 });
    s.addText(st[0], { x: x + 0.24, y: 2.8, w: 3.4, h: 0.4, fontSize: 15, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0 });
    s.addText(st[1], { x: x + 0.24, y: 3.24, w: 3.4, h: 0.7, fontSize: 12.5, fontFace: FONT_BODY, color: MUTED, margin: 0, valign: "top" });
    if (i < 2) s.addText("→", { x: x + 3.92, y: 3.05, w: 0.36, h: 0.5, fontSize: 20, fontFace: FONT_BODY, bold: true, color: ACCENT_D, margin: 0, align: "center" });
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 4.32, w: CW, h: 0.62, fill: { color: TINT2 }, line: { color: "BFDCCE", width: 0.75 }, rectRadius: 0.06 });
  s.addText([
    { text: "自动治理 + 人工审核：", options: { bold: true, color: ACCENT_D } },
    { text: "六域决策信封全量落库——auto_applied 自动入图 · proposal 进管理后台审核队列人工复核 · dict-guard 动态词典拦截，每条决策可回放审计", options: { color: INK } },
  ], { x: M + 0.25, y: 4.32, w: CW - 0.5, h: 0.62, fontSize: 12.5, fontFace: FONT_BODY, valign: "middle", margin: 0 });
  s.addText("评测闭环：110 条正式黄金集（人工标注）LLM 盲审", { x: M, y: 5.1, w: 8, h: 0.35, fontSize: 14, fontFace: FONT_BODY, bold: true, color: INK, margin: 0 });
  stat(s, M, 5.5, 2.6, CAL.JD_F1, "技能 aligned F1（目标 ≥0.90）", { numSize: 36, color: ACCENT_D });
  stat(s, M + 2.8, 5.5, 2.6, "< 1%", "幻觉 FP 率", { numSize: 36 });
  stat(s, M + 5.6, 5.5, 2.6, "0.8865", "raw F1（未对齐口径）", { numSize: 36 });
  s.addText("口径说明：跨源一致性为每日计算并在看板展示（透明可查），自动硬门控列入后续路线图", { x: 8.6, y: 5.62, w: 4.2, h: 1.1, fontSize: 11.5, fontFace: FONT_BODY, color: MUTED, margin: 0, valign: "top" });
  s.addText("Source: 110 条 gold 人工标注盲审（词面真值对齐口径）", { x: M, y: 6.85, w: 8, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: "9AA9BA", margin: 0 });
})();

/* ========== 8 人岗匹配引擎 ========== */
(() => {
  const s = base();
  title(s, "MATCHING ENGINE", "人岗匹配引擎 · 技能级三维评分");
  shot43(s, "image-matching_4x3.jpg", 6.7, 1.72, 6.1);
  s.addText("▲ 三维评分 + 语义增强 · 差距三态诊断 + 证据链追溯", { x: 6.7, y: 6.4, w: 6.1, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  bullets(s, [
    "三维加权：必备技能（must/nice 加权命中）+ 经验 + 学历，差距三态诊断",
    "语义增强：SBERT 多语言向量 + Bradley-Terry 权重学习（Spearman 0.88）",
    "纠偏机制：技能通胀修正（CII）+ 时效衰减 + 跨域降权",
    "JD 级评分口径：推荐列表与详情同源同分——对齐「最匹配的那条真实 JD」",
    "双模式：批量推荐（Top-N）+ 单点比对（差距分析 + 学习路径）",
  ], M, 1.85, 5.9, 4.6, { fontSize: 14 });
})();

/* ========== 9 演化发现 ========== */
(() => {
  const s = base();
  title(s, "EVOLUTION", "演化发现 · 岗位能力生命周期");
  shot(s, "image-evolution_16x9.jpg", 6.7, 1.72, 6.1);
  s.addText("▲ 双信号检测：Z-score + MoM · 新兴/衰退趋势实时追踪", { x: 6.7, y: 5.28, w: 6.1, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  bullets(s, [
    "双信号检测：Z-score 统计门控 + 环比 MoM 辅助，抗波动打标",
    "岗位状态机：emerging → stable → declining（置信度阈值 + RAG 接地）",
    "技术热点观察池（arXiv / GitHub 信号）→ 自动升级 candidate 候选",
    "新岗位发现页：候选岗位 + 技能增减 diff，人工审核后入图",
  ], M, 1.85, 5.9, 3.5, { fontSize: 14 });
  // 状态机小流程
  const st = [["emerging", ACCENT], ["stable", NAVY_MID], ["declining", "B4552D"]];
  st.forEach((n, i) => {
    const x = M + i * 1.95;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 5.7, w: 1.55, h: 0.46, fill: { color: n[1] }, rectRadius: 0.23 });
    s.addText(n[0], { x, y: 5.7, w: 1.55, h: 0.46, fontSize: 12.5, fontFace: FONT_BODY, color: "FFFFFF", align: "center", valign: "middle", margin: 0 });
    if (i < 2) s.addText("→", { x: x + 1.57, y: 5.7, w: 0.36, h: 0.46, fontSize: 16, fontFace: FONT_BODY, bold: true, color: MUTED, align: "center", valign: "middle", margin: 0 });
  });
  s.addText("媒介落差指数 MLI：四维信号综合判定产业拐点", { x: M, y: 6.45, w: 5.9, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, margin: 0 });
})();

/* ========== 10 个性化学习路径 ========== */
(() => {
  const s = base();
  title(s, "LEARNING PATH", "个性化学习路径 · 从差距到课程");
  shot43(s, "image-learningpath_4x3.jpg", 6.7, 1.72, 6.1);
  s.addText("▲ 先修链拓扑排序 + 学时甘特呈现 · 课程 LEARNABLE_VIA 匹配", { x: 6.7, y: 6.4, w: 6.1, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  bullets(s, [
    "差距诊断：missing / weak 分级 + ROI 优先级排序（需求度 × 趋势 ÷ 成本）",
    "先修链展开（242 技能字典）+ 拓扑排序，分阶段学时甘特呈现",
    "课程匹配：图谱 LEARNABLE_VIA + 语义兜底 + 中英词面豁免 + 灰色带质量门控",
    "专家评审定稿：30 案例合理性 96.7%（course 语义 0.879 / hours 0.916）",
  ], M, 1.85, 5.9, 4.6, { fontSize: 14 });
})();

/* ========== 11 前端可视化 ========== */
(() => {
  const s = base();
  title(s, "FRONTEND", "前端可视化 · React 19 + ECharts");
  const views = [
    ["image-graph_16x9.jpg", "全景视图 panorama", "2D 力导向异构图谱\n≥100 节点 @ 60fps"],
    ["image-techstack_16x9.jpg", "技术栈视图 techStack", "技能 → 岗位关联聚合\n热点技术栈一屏总览"],
    ["image-portrait_16x9.jpg", "岗位画像 positionPortrait", "单岗位能力雷达\n技能要求 + 差距诊断"],
  ];
  views.forEach((v, i) => {
    const w = 3.95, x = M + i * (w + 0.14);
    shot(s, v[0], x, 1.75, w);
    s.addText(v[1], { x, y: 4.15, w, h: 0.35, fontSize: 14.5, fontFace: FONT_BODY, bold: true, color: NAVY, align: "center", margin: 0 });
    s.addText(v[2], { x, y: 4.52, w, h: 0.62, fontSize: 11.5, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  });
  const extras = [
    ["匹配可视化", "环形图 · 雷达图 · 热力图 · 甘特图"],
    ["响应式适配", "深浅双主题 · 平板/移动 2D 固定"],
    ["3D 可选", "动态加载 · WebGL2 不可用自动降级 2D"],
  ];
  extras.forEach((v, i) => {
    const w = 3.95, x = M + i * (w + 0.14), y = 5.45;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 1.05, fill: { color: TINT2 }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.06 });
    s.addText(v[0], { x: x + 0.25, y: y + 0.14, w: w - 0.5, h: 0.32, fontSize: 13, fontFace: FONT_BODY, bold: true, color: NAVY, margin: 0 });
    s.addText(v[1], { x: x + 0.25, y: y + 0.5, w: w - 0.5, h: 0.42, fontSize: 11.5, fontFace: FONT_BODY, color: MUTED, margin: 0 });
  });
  s.addText("▲ 三视图均为界面概念图，真实截图以系统演示为准", { x: M, y: 6.75, w: CW, h: 0.3, fontSize: 11, fontFace: FONT_BODY, color: "9AA9BA", align: "center", margin: 0 });
})();

/* ========== 12 性能与压测 ========== */
(() => {
  const s = base();
  title(s, "PERFORMANCE", "性能与压测 · P95 < 2s 目标达成");
  s.addChart(p.charts.BAR, [{
    name: "实测 P95 (ms)",
    labels: ["panorama（图谱全景）", "search（全文检索）"],
    values: [430, 390],
  }], {
    x: M, y: 1.8, w: 6.6, h: 4.4, barDir: "col",
    chartColors: [NAVY_MID, ACCENT],
    chartArea: { fill: { color: "FFFFFF" } },
    catAxisLabelColor: MUTED, catAxisLabelFontSize: 12, catAxisLabelFontFace: FONT_BODY,
    valAxisLabelColor: MUTED, valAxisLabelFontSize: 11, valAxisLabelFontFace: FONT_BODY,
    valGridLine: { color: "E4EAF0", size: 0.5 }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: NAVY, dataLabelFontSize: 13, dataLabelFontFace: FONT_BODY,
    showLegend: false, valAxisMaxVal: 2000,
  });
  s.addText("Source: 100 并发压测基线（docs/perf_baseline_20260815.md），目标 P95 < 2000ms", { x: M, y: 6.35, w: 7.2, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: "9AA9BA", margin: 0 });
  stat(s, 8.0, 1.9, 4.6, "< 500ms", "100 并发实测 P95（目标 < 2s）", { numSize: 44, color: ACCENT_D, numH: 0.9 });
  bullets(s, [
    "single-flight 缓存穿透合并",
    "search 60s 缓存 + Neo4j 连接池扩容",
    "全文检索 < 500ms · pgvector < 300ms",
    "Redis 缓存命中率 ≥ 85%",
  ], 8.2, 3.6, 4.3, 2.8, { fontSize: 13.5, gap: 8 });
})();

/* ========== 13 评测体系 ========== */
(() => {
  const s = base();
  title(s, "EVALUATION", "评测体系 · 三层闭环");
  bullets(s, [
    "第一层 关键词基线（离线确定性）→ 第二层 LLM 盲审（人工 gold）→ 第三层 专家定稿",
    "JD 解析六维评测：title / skills / bonus / education / experience / core_duties",
    "全部指标均以人工标注或专家定稿为真值，非自评",
  ], M, 1.5, CW, 1.35, { fontSize: 14 });
  const metrics = [
    [CAL.JD_F1, "JD 解析 aligned F1", CAL.JD_F1_NOTE],
    [CAL.RESUME_F1, "简历解析 F1", "50 条"],
    [CAL.MATCH_ACC, "人岗匹配 Acc", "384 对黄金集含学历维度（BT v4）"],
    [CAL.LEARN, "学习路径合理性", "30 案例专家定稿"],
  ];
  metrics.forEach((m, i) => {
    const w = 2.95, x = M + i * (w + 0.14);
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 3.1, w, h: 2.15, fill: { color: TINT2 }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.08 });
    s.addText(m[0], { x, y: 3.35, w, h: 0.85, fontSize: 40, fontFace: FONT_HEAD, bold: true, color: i === 0 ? ACCENT_D : NAVY, align: "center", margin: 0 });
    s.addText(m[1], { x, y: 4.28, w, h: 0.35, fontSize: 14, fontFace: FONT_BODY, bold: true, color: INK, align: "center", margin: 0 });
    s.addText(m[2], { x, y: 4.66, w, h: 0.35, fontSize: 11.5, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  });
  s.addText("Source: 评测产物记录代码版本与 gold 口径；aligned=别名豁免 + 词面真值对齐口径", { x: M, y: 5.65, w: 12, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: "9AA9BA", margin: 0 });
})();

/* ========== 14 安全与合规 ========== */
(() => {
  const s = base();
  title(s, "SECURITY", "安全与合规");
  const rows = [
    ["PII 脱敏", "简历隔离存储 + 敏感字段掩码后方送入 LLM，符合 PIPL / GDPR"],
    ["认证与权限", "RBAC 三级角色（admin/user/guest）+ 双 Token + 登出黑名单 + 弱口令门禁"],
    ["接口防护", "限流中间件 100 req/min/IP + 统一错误码契约（openapi.yaml 单一事实源）"],
    ["数据合规", "仅采集公开元数据 + robots 遵循 + 学术非商业用途声明"],
  ];
  rows.forEach((r, i) => {
    const y = 1.75 + i * 1.28;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 1.9, h: 0.62, fill: { color: TINT }, line: { color: "C9D7E5", width: 0.75 }, rectRadius: 0.08 });
    s.addText(r[0], { x: M, y, w: 1.9, h: 0.62, fontSize: 13.5, fontFace: FONT_BODY, bold: true, color: NAVY, align: "center", valign: "middle", margin: 0 });
    s.addText(r[1], { x: 2.75, y: y + 0.06, w: 10.0, h: 0.55, fontSize: 14, fontFace: FONT_BODY, color: INK, margin: 0, valign: "middle" });
    if (i < 3) s.addShape(p.shapes.LINE, { x: 2.75, y: y + 0.98, w: 9.9, h: 0, line: { color: "E3EAF2", width: 0.75 } });
  });
})();

/* ========== 15 数据规模 ========== */
(() => {
  const s = base();
  title(s, "DATA SCALE", "数据规模（实况快照）");
  const tiles = [
    [CAL.SKILLS, "Skill 技能节点"], [CAL.POSITIONS, "Position 岗位节点"], ["1,754", "课程（三门平台）"],
    ["11,115", "jd_raw 全量抽取"], ["242", "先修链技能字典"], ["110 + 50", "JD / 简历黄金集"],
  ];
  tiles.forEach((t, i) => {
    const w = 3.95, x = M + (i % 3) * (w + 0.14), y = 1.8 + Math.floor(i / 3) * 2.1;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 1.85, fill: { color: i === 0 ? TINT : TINT2 }, line: { color: "D7E2ED", width: 0.75 }, rectRadius: 0.08 });
    s.addText(t[0], { x, y: y + 0.28, w, h: 0.8, fontSize: 38, fontFace: FONT_HEAD, bold: true, color: NAVY, align: "center", margin: 0 });
    s.addText(t[1], { x, y: y + 1.14, w, h: 0.4, fontSize: 13, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
  });
  s.addText("另有：" + CAL.DOMAINS + "（" + CAL.DOMAINS_FULL + "）· 技能分类权威覆盖 23.9%（用户可见视图 91.7%）· 匹配黄金对 v1/v2 684 组", { x: M, y: 6.15, w: CW, h: 0.6, fontSize: 12.5, fontFace: FONT_BODY, color: MUTED, margin: 0, valign: "top" });
  s.addText("注：09-04 实查 · 数据截至 " + CAL.SNAP + " 快照（" + CAL.NODES_EDGES + "）", { x: M, y: 6.85, w: CW, h: 0.3, fontSize: 12, fontFace: FONT_BODY, color: "9AA9BA", margin: 0 });
})();

/* ========== 18 关键成果数据 ========== */
(() => {
  const s = base();
  title(s, "RESULTS", "关键成果数据");
  const res = [
    [CAL.JD_F1, "JD 解析 aligned F1", "≥0.90 达标"],
    [CAL.MATCH_ACC, "人岗匹配 Acc", "384 对黄金集含学历维度（BT v4）"],
    [CAL.LEARN, "学习路径合理性", "30 案例专家定稿"],
    ["91.7%", "技能分类视图覆盖", "权威覆盖 12.8% → 23.9%"],
    [CAL.P95, "图谱全景 P95", "100 并发 · 目标 <2s"],
    ["480+", "PR · ~71k 行", "CI 全绿 · 六轮审查"],
  ];
  res.forEach((r, i) => {
    const w = 3.95, x = M + (i % 3) * (w + 0.14), y = 1.8 + Math.floor(i / 3) * 2.4;
    s.addText(r[0], { x, y, w, h: 0.95, fontSize: 46, fontFace: FONT_HEAD, bold: true, color: i === 0 ? ACCENT_D : NAVY, align: "center", margin: 0 });
    s.addText(r[1], { x, y: y + 0.98, w, h: 0.38, fontSize: 15, fontFace: FONT_BODY, bold: true, color: INK, align: "center", margin: 0 });
    s.addText(r[2], { x, y: y + 1.38, w, h: 0.35, fontSize: 12, fontFace: FONT_BODY, color: MUTED, align: "center", margin: 0 });
    if (i < 5) s.addShape(p.shapes.LINE, { x: x + 0.25, y: y + 1.95, w: w - 0.5, h: 0, line: { color: "E3EAF2", width: 0.75 } });
  });
})();

/* ========== 19 创新与差异化 ========== */
(() => {
  const s = base();
  title(s, "DIFFERENTIATION", "创新与差异化");
  const cmp = [
    ["岗位能力画像", "静态文档，更新以月/年计", "动态演化图谱，随技术趋势自我更新"],
    ["数据可信度", "单源 JD 关键词，信号失真即污染", "四源交叉验证 + 每日一致性看板"],
    ["匹配粒度", "关键词命中，粗粒度打分", "技能级三维评分 + 差距三态诊断"],
    ["后续动作", "匹配即终点", "差距 → 先修链 → 课程 → 学时的闭环路径"],
    ["LLM 可信度", "生成即采信", "三道防线 + 全量决策可回放审计"],
  ];
  s.addText("传统做法", { x: 3.1, y: 1.55, w: 4.2, h: 0.4, fontSize: 15, fontFace: FONT_BODY, bold: true, color: MUTED, align: "center", margin: 0 });
  s.addText("智岗罗盘", { x: 7.9, y: 1.55, w: 5.0, h: 0.4, fontSize: 15, fontFace: FONT_BODY, bold: true, color: ACCENT_D, align: "center", margin: 0 });
  cmp.forEach((r, i) => {
    const y = 2.05 + i * 0.98;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 2.4, h: 0.72, fill: { color: TINT }, line: { color: "C9D7E5", width: 0.75 }, rectRadius: 0.06 });
    s.addText(r[0], { x: M, y, w: 2.4, h: 0.72, fontSize: 13.5, fontFace: FONT_BODY, bold: true, color: NAVY, align: "center", valign: "middle", margin: 0 });
    s.addText(r[1], { x: 3.1, y, w: 4.2, h: 0.72, fontSize: 13, fontFace: FONT_BODY, color: MUTED, align: "center", valign: "middle", margin: 0 });
    s.addText("→", { x: 7.32, y, w: 0.5, h: 0.72, fontSize: 18, fontFace: FONT_BODY, bold: true, color: ACCENT_D, align: "center", valign: "middle", margin: 0 });
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 7.9, y, w: 5.0, h: 0.72, fill: { color: TINT2 }, line: { color: "BFDCCE", width: 0.75 }, rectRadius: 0.06 });
    s.addText(r[2], { x: 8.05, y, w: 4.7, h: 0.72, fontSize: 13, fontFace: FONT_BODY, bold: true, color: INK, valign: "middle", margin: 0 });
  });
})();

/* ========== 20 总结与展望（深底收尾） ========== */
(() => {
  const s = base(true);
  s.addText("SUMMARY", { x: M, y: 1.0, w: 6, h: 0.35, fontSize: 12, fontFace: FONT_BODY, color: "8FD4BC", bold: true, charSpacing: 3, margin: 0 });
  s.addText("全链路闭环 · 评测达标 · 演示就绪", { x: M, y: 1.4, w: CW, h: 0.8, fontSize: 34, fontFace: FONT_HEAD, bold: true, color: "FFFFFF", margin: 0 });
  bullets(s, [
    "采集 → 图谱 → 抽取 → 匹配 → 演化 → 学习路径，55 天完成全链路真实数据闭环",
    "四项核心指标全部达标：JD 解析 " + CAL.JD_F1 + " · 匹配 " + CAL.MATCH_ACC + " · 学习路径 " + CAL.LEARN + " · P95 " + CAL.P95,
    "展望：新岗位自动发现（种子引导 + 自动判定）· 数字化迁移领域扩展",
  ], M, 2.6, CW, 1.9, { fontSize: 15.5, color: "D8E4F0", gap: 12 });
  s.addText("感谢各位评委聆听", { x: M, y: 5.4, w: CW, h: 0.9, fontSize: 30, fontFace: FONT_HEAD, bold: true, color: "FFFFFF", margin: 0 });
  s.addText("智岗罗盘团队 · XH-202621 · 2026.09", { x: M, y: 6.4, w: CW, h: 0.4, fontSize: 13, fontFace: FONT_BODY, color: "9FB4CC", margin: 0 });
})();

p.writeFile({ fileName: "智岗罗盘_答辩PPT_终稿.pptx" }).then(() => console.log("PPT done: 19 slides"));
