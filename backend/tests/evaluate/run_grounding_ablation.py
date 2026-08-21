"""Grounding 防线消融评测：幻觉拦截率对比（答辩展示用 P0 基座）。

对比"无 Grounding 控制"vs"开启完整防线"（NLI 矛盾检测 + 重采样 + 截断回退，
见 app/services/discovery/nli_guard.py 与 grounding.py）在确定性幻觉金标准上的
幻觉拦截率，并导出瀑布图（纯 Python SVG，不依赖 matplotlib）。

背景：run_baseline.py / run_rag_jd_eval.py 度量的是**抽取质量**（岗位名/技能
F1），不产出"幻觉拦截率"。本脚本补上缺失的**幻觉评测基座**：构造
(reference 基座, 幻觉草案) 金标准，用确定性伪 LLM 注入草案，验证防线是否
截断回退；控制组（忠实草案）验证不误拦截。

幻觉信号覆盖（与 nli_guard 三信号一一对应）：
- 否定极性翻转（negation_asymmetry）→ 确认级 → 首稿直接截断
- 学历量级冲突（degree_level_conflict）→ 确认级 → 首稿直接截断
- 否定断言无基座（negation_assertion_without_grounding）→ 可疑级 → 重采样后仍可疑 → 截断
- 追加式编造（无对立断言，当前 NLI 覆盖不到）→ 残余漏网（诚实展示防线边界，
  由 P1 confidence 标量化防线兜底）

用法：
    python tests/evaluate/run_grounding_ablation.py

产物：
    backend/data/evaluate/grounding_ablation.json   （评测数据）
    backend/data/evaluate/ablation_waterfall.svg    （瀑布图）
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.discovery import grounding  # noqa: E402
from app.services.discovery.nli_guard import detect_contradiction  # noqa: E402

OUT_DIR = ROOT / "data" / "evaluate"
JSON_PATH = OUT_DIR / "grounding_ablation.json"
SVG_PATH = OUT_DIR / "ablation_waterfall.svg"

# 无 Grounding 控制（旧管线）幻觉放行率：LLM 草案不经任何校验直接通过
BASELINE_INTERCEPT_RATE = 0.0


class _FakeLLM:
    """确定性伪 LLM：固定返回注入的草案；重采样时"固执"地重复同一草案
    （最坏情形——验证软门控在 LLM 不配合时仍能截断回退）。

    calls 记录调用次数：1=首稿即截断/放行；2=首稿可疑触发重采样。
    """

    def __init__(self, draft_text: str) -> None:
        self.draft_text = draft_text
        self.calls = 0

    def extract_structured(self, prompt, schema, system_prompt=None):
        self.calls += 1
        return schema(text=self.draft_text)


# ── 幻觉金标准（确定性子集，无 LLM/DB 依赖，CI 可复跑）──
# 每条：(id, 参考基座 premise, 幻觉/忠实草案 draft, 预期拦截档位)
# 预期档位：negation_asymmetry / degree_level_conflict / resample / pass(漏网)
HALLUCINATION_CASES = [
    # ① 否定极性翻转（同语言高重合 → 确认级 → 首稿截断）
    (
        "neg-1",
        "该岗位要求候选人熟练掌握 Python 编程，负责推荐系统开发与模型训练，具备扎实的机器学习基础。",
        "该岗位要求候选人熟练掌握 Python 编程，负责推荐系统开发与模型训练，无需任何机器学习基础。",
        "negation_asymmetry",
    ),
    (
        "neg-2",
        "岗位要求熟悉容器化部署与 Kubernetes 集群管理，具备微服务架构经验。",
        "岗位要求熟悉容器化部署与 Kubernetes 集群管理，不具备微服务架构经验。",
        "negation_asymmetry",
    ),
    (
        "neg-3",
        "岗位负责爬虫系统开发，要求掌握 Scrapy 框架与反爬对抗经验。",
        "岗位负责爬虫系统开发，不要求掌握 Scrapy 框架与反爬对抗经验。",
        "negation_asymmetry",
    ),
    # ② 学历量级冲突（含跨语言，英文基座 → 中文草案，确认级 → 首稿截断）
    (
        "deg-1",
        "岗位要求本科及以上学历，负责数据分析与 SQL 开发，掌握可视化工具。",
        "岗位负责数据分析与 SQL 开发，高中学历即可胜任，掌握可视化工具。",
        "degree_level_conflict",
    ),
    (
        "deg-2",
        "岗位要求硕士学历，负责大模型训练与推理优化。",
        "岗位负责大模型训练与推理优化，大专学历即可。",
        "degree_level_conflict",
    ),
    (
        "deg-3",
        "This position requires a doctoral degree and expertise in reinforcement learning.",
        "该岗位需要本科学历即可，负责强化学习相关工作。",
        "degree_level_conflict",
    ),
    # ③ 否定断言无基座支撑（跨语言低重合，可疑级 → 重采样后仍可疑 → 截断）
    (
        "neg-g-1",
        "Machine learning engineers build and deploy ML models for production systems, working with large-scale data pipelines.",
        "机器学习工程师不需要了解任何编程语言。",
        "resample",
    ),
    (
        "neg-g-2",
        "DevOps engineers manage CI/CD pipelines and cloud infrastructure automation.",
        "DevOps 工程师完全不需要了解任何云基础设施知识。",
        "resample",
    ),
    (
        "neg-g-3",
        "岗位要求掌握机器学习基础与数据处理能力。",
        "该职位完全不涉及机器学习相关技能，候选人无需任何算法背景。",
        "resample",
    ),
    # ④ 追加式编造（无对立断言/学历降级，当前 NLI 覆盖不到 → 漏网）
    (
        "miss-1",
        "数据分析师负责业务数据报表开发，使用 SQL 提取数据并输出分析结论。",
        "数据分析师负责业务数据报表开发，使用 SQL 提取数据并输出分析结论，且要求十年以上专家级经验。",
        "pass",
    ),
    (
        "miss-2",
        "前端工程师负责 Web 应用界面开发与交互优化。",
        "前端工程师负责 Web 应用界面开发与交互优化，同时必须通过内部保密级别最高的政审认证。",
        "pass",
    ),
]

# 控制组：忠实草案，不应被误拦截（false_interception 必须为 0）
CONTROL_CASES = [
    (
        "ctrl-1",
        "算法工程师负责推荐系统排序模型开发，要求掌握深度学习与 Python 编程。",
        "算法工程师负责推荐系统排序模型开发，要求掌握深度学习与 Python 编程。",
    ),
    (
        "ctrl-2",
        "This role involves developing and maintaining web applications using modern frontend frameworks.",
        "该岗位负责使用现代前端框架开发与维护 Web 应用。",
    ),
    (
        "ctrl-3",
        "岗位要求硕士及以上学历，精通图数据库与知识图谱构建。",
        "岗位要求硕士及以上学历，精通图数据库与知识图谱构建，负责知识图谱落地。",
    ),
]

# 拦截档位 → 瀑布图阶段名
STAGE_LABELS = {
    "negation_asymmetry": "否定极性翻转拦截",
    "degree_level_conflict": "学历量级冲突拦截",
    "resample": "重采样复核拦截",
    "pass": "残余幻觉（漏网）",
}


async def _run_case(premise: str, draft: str) -> tuple[object, int, object]:
    """跑真实生产链路 _generate_definition（软门控全部逻辑），返回
    (DefinitionResult, llm 调用次数, NLI 判定结果)。

    _DefinitionResult 不含 signals/label/score 明细，另行对同一 (premise,
    draft) 调 detect_contradiction（伪 LLM 确定性返回注入草案，判定输入与
    生产链路完全一致）获取信号用于档位分类与审计。
    """
    llm = _FakeLLM(draft)
    res = await grounding._generate_definition(
        "测试岗位", None, {"definition": premise}, llm
    )
    nli = detect_contradiction(premise, draft)
    return res, llm.calls, nli


def _stage_of(res, calls: int, nli) -> str:
    """将真实判定映射到瀑布图阶段（与预期档位对齐）。"""
    if not res.nli_contradicted:
        return "pass"
    if calls >= 2:
        return "resample"
    first_sig = (nli.signals or [""])[0].split("(")[0]
    if first_sig == "negation_asymmetry":
        return "negation_asymmetry"
    if first_sig == "degree_level_conflict":
        return "degree_level_conflict"
    # 其余确认级（如 negation_asymmetry_reverse）归入否定翻转档位
    return "negation_asymmetry"


async def _evaluate() -> dict:
    """跑全量金标准，产出逐条明细 + 汇总指标。"""
    details = []
    for cid, premise, draft, expected in HALLUCINATION_CASES:
        res, calls, nli = await _run_case(premise, draft)
        actual = _stage_of(res, calls, nli)
        details.append({
            "id": cid,
            "kind": "hallucination",
            "premise": premise,
            "draft": draft,
            "expected": expected,
            "actual": actual,
            "intercepted": res.nli_contradicted,
            "nli_label": nli.label,
            "nli_score": nli.score,
            "signals": nli.signals,
            "source": res.source,
            "llm_calls": calls,
        })

    controls = []
    for cid, premise, draft in CONTROL_CASES:
        res, calls, nli = await _run_case(premise, draft)
        controls.append({
            "id": cid,
            "kind": "control",
            "intercepted": res.nli_contradicted,
            "nli_label": nli.label,
            "nli_score": nli.score,
            "signals": nli.signals,
            "source": res.source,
            "llm_calls": calls,
        })

    total = len(HALLUCINATION_CASES)
    intercepted = [d for d in details if d["intercepted"]]
    residual = [d for d in details if not d["intercepted"]]
    false_intercepted = [c for c in controls if c["intercepted"]]

    from collections import Counter

    stage_counts = Counter(d["actual"] for d in intercepted)
    mismatch = [d for d in details if d["actual"] != d["expected"]]

    metrics = {
        "total_hallucination": total,
        "intercepted": len(intercepted),
        "residual": len(residual),
        "interception_rate": round(len(intercepted) / total, 4) if total else 0.0,
        "baseline_interception_rate": BASELINE_INTERCEPT_RATE,
        "false_interception": len(false_intercepted),
        "stage_counts": dict(stage_counts),
        "stage_order": ["negation_asymmetry", "degree_level_conflict", "resample"],
    }
    return {
        "metrics": metrics,
        "details": details,
        "controls": controls,
        "mismatch": mismatch,
    }


# ────────────────────────────────────────────────────────────────────
# 纯 Python SVG 瀑布图渲染（无 matplotlib 依赖）
# ────────────────────────────────────────────────────────────────────

_W = 1080
_H = 660
_M_LEFT = 80
_M_RIGHT = 40
_M_TOP = 96
_M_BOTTOM = 104
_PLOT_W = _W - _M_LEFT - _M_RIGHT
_PLOT_H = _H - _M_TOP - _M_BOTTOM
_FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"
_C_TOTAL = "#607d8b"
_C_DEC = "#2e7d32"
_C_RESID = "#c62828"
_C_BASE = "#f57c00"
_C_GRID = "#e0e0e0"
_C_TEXT = "#212121"
_C_MUTED = "#757575"


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _text(x, y, s, size=13, anchor="middle", fill=_C_TEXT, weight="normal", dy=None):
    dy_attr = f' dy="{dy}"' if dy else ''
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{_FONT}" font-size="{size}" '
        f'text-anchor="{anchor}" fill="{fill}" font-weight="{weight}"{dy_attr}>{_esc(s)}</text>'
    )


def _rect(x, y, w, h, fill, stroke="none", sw=0, opacity=1.0):
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{opacity}"/>'
    )


def _line(x1, y1, x2, y2, stroke=_C_GRID, sw=1, dash=""):
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{sw}"{dash_attr}/>'
    )


def _waterfall_bars(data: dict) -> list:
    """把指标展开为瀑布图条目标。

    条目标：(key, label, value, kind)
    - total: 幻觉草案总量（基准条）
    - dec:   拦截档位（浮动递减条）
    - resid: 残余幻觉（终值条）
    """
    m = data["metrics"]
    n = m["total_hallucination"]
    bars = [("total", "幻觉草案\n总量", n, "total")]
    cumulative = n
    for stage in m["stage_order"]:
        cnt = m["stage_counts"].get(stage, 0)
        if cnt > 0:
            bars.append((f"dec:{stage}", STAGE_LABELS[stage], cnt, "dec"))
            cumulative -= cnt
    bars.append(("resid", "残余幻觉\n（漏网）", cumulative, "resid"))
    return bars


def render_waterfall_svg(data: dict) -> str:
    """渲染瀑布图 SVG 字符串。"""
    bars = _waterfall_bars(data)
    m = data["metrics"]
    n = m["total_hallucination"] or 1
    n_cat = len(bars)
    slot_w = _PLOT_W / n_cat
    bar_w = min(96.0, slot_w * 0.62)
    y0 = _M_TOP + _PLOT_H
    max_v = n

    def y_of(v: float) -> float:
        return y0 - (v / max_v) * _PLOT_H

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}">'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    # 标题 + 副标题
    parts.append(_text(_W / 2, 46, "Grounding 防线消融：幻觉拦截率对比", 24, "middle", _C_TEXT, "bold"))
    parts.append(_text(
        _W / 2, 74,
        "无 Grounding 控制（旧管线：幻觉全部放行） vs 开启完整防线（NLI 矛盾检测 + 重采样 + 截断回退）",
        14, "middle", _C_MUTED,
    ))

    # 网格线 + Y 轴刻度（计数）
    for i in range(5):
        v = (max_v * i) / 4
        gy = y_of(v)
        parts.append(_line(_M_LEFT, gy, _W - _M_RIGHT, gy, _C_GRID, 1, "4,4"))
        parts.append(_text(_M_LEFT - 10, gy + 4, str(int(round(v))), 12, "end", _C_MUTED))

    # 无 Grounding 控制基准线（100% 放行）
    base_y = y_of(n)
    parts.append(_line(_M_LEFT, base_y, _W - _M_RIGHT, base_y, _C_BASE, 2, "8,4"))
    parts.append(_text(_W - _M_RIGHT - 6, base_y - 6, "无 Grounding 控制：拦截率 0%（全部放行）", 12, "end", _C_BASE))

    # 瀑布条
    cumulative = n
    for idx, (key, label, value, kind) in enumerate(bars):
        cx = _M_LEFT + slot_w * (idx + 0.5)
        x = cx - bar_w / 2
        pct = value / n * 100
        if kind == "total":
            top = y_of(value)
            parts.append(_rect(x, top, bar_w, y0 - top, _C_TOTAL))
            parts.append(_text(cx, top - 10, f"{value}（100%）", 13, "middle", _C_TEXT, "bold"))
        elif kind == "dec":
            top = y_of(cumulative)
            bottom = y_of(cumulative - value)
            # 连接线（阶梯）：从上一累计水平到本条起始水平
            parts.append(_line(_M_LEFT + slot_w * idx, top, x, top, "#9e9e9e", 1, "3,3"))
            parts.append(_rect(x, top, bar_w, bottom - top, _C_DEC))
            parts.append(_text(cx, (top + bottom) / 2 + 4, f"−{value}", 15, "middle", "#ffffff", "bold"))
            cumulative -= value
        elif kind == "resid":
            top = y_of(value)
            parts.append(_rect(x, top, bar_w, y0 - top, _C_RESID))
            parts.append(_text(cx, top - 10, f"{value}（{pct:.0f}%）", 13, "middle", _C_RESID, "bold"))
            parts.append(_line(x - slot_w * 0.2, y_of(cumulative), x, y_of(cumulative), "#9e9e9e", 1, "3,3"))

        # 类别标签
        for j, seg in enumerate(label.split("\n")):
            parts.append(_text(cx, y0 + 28 + j * 20, seg, 13, "middle", _C_TEXT, "bold" if kind == "resid" else "normal"))

    # 图例
    lx = _M_LEFT + 10
    ly = _H - _M_BOTTOM + 76
    legend = [
        (_C_TOTAL, "幻觉草案总量"),
        (_C_DEC, "防线拦截（各档位）"),
        (_C_RESID, "残余幻觉（漏网）"),
        (_C_BASE, "无 Grounding 控制基准线"),
    ]
    for i, (color, name) in enumerate(legend):
        gx = lx + i * 250
        parts.append(_rect(gx, ly - 9, 16, 16, color))
        parts.append(_text(gx + 22, ly, name, 12, "start", _C_TEXT))

    # 底部结论条
    rate = m["interception_rate"]
    resid = m["residual"]
    false_inter = m["false_interception"]
    parts.append(_line(_M_LEFT, _H - _M_BOTTOM + 46, _W - _M_RIGHT, _H - _M_BOTTOM + 46, _C_GRID, 1))
    parts.append(_text(
        _W / 2, _H - _M_BOTTOM + 68,
        f"幻觉拦截率：无 Grounding 0% → 完整防线 {rate:.1%}（拦截 {m['intercepted']}/{n}，残余 {resid}，"
        f"控制组误拦截 {false_inter}）",
        15, "middle", _C_TEXT, "bold",
    ))

    parts.append("</svg>")
    return "\n".join(parts)


def _print_report(data: dict) -> None:
    m = data["metrics"]
    print("=" * 64)
    print("Grounding 防线消融评测报告（幻觉拦截率）")
    print("=" * 64)
    print(f"幻觉金标准样本：{m['total_hallucination']}")
    print(f"  无 Grounding 控制拦截率：{m['baseline_interception_rate']:.1%}（旧管线幻觉全部放行）")
    print(f"  完整防线拦截率：{m['interception_rate']:.1%}（拦截 {m['intercepted']}，残余 {m['residual']}）")
    print(f"  控制组误拦截：{m['false_interception']}（应 = 0）")
    print("\n拦截档位明细：")
    for stage in m["stage_order"]:
        print(f"  {STAGE_LABELS[stage]:<14} {m['stage_counts'].get(stage, 0)}")
    if data["mismatch"]:
        print("\n[WARN] 与预期档位不一致：")
        for d in data["mismatch"]:
            print(f"  {d['id']}: expected={d['expected']} actual={d['actual']} "
                  f"label={d['nli_label']} score={d['nli_score']} signals={d['signals']}")
    else:
        print("\n[OK] 全部与预期档位一致")


async def _main_async() -> int:
    data = await _evaluate()
    _print_report(data)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    SVG_PATH.write_text(render_waterfall_svg(data), encoding="utf-8")
    print(f"\n数据导出：{JSON_PATH.relative_to(ROOT)}")
    print(f"瀑布图导出：{SVG_PATH.relative_to(ROOT)}")
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    sys.exit(main())
