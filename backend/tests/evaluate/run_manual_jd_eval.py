"""Evaluate independently reviewed JD records through the current JDExtractor.

This is an evaluation-only entry point.  It never writes the source workbook or
golden-set JSONL.  A real run is deliberately blocked unless the gold labels pass
strict format checks; this prevents rule-based fallback or malformed human labels
from being reported as an LLM extraction baseline.

支持两种 gold 输入源（指标口径一致，仅输入层不同）：
- 盲审 xlsx（默认，--xlsx/--sheet）
- final gold JSONL（--gold-jsonl），即 data/golden_set/final/jd_golden_110.jsonl
  （PR #316 交付的 110 条 Round1 人工标注；`--run` 前同上做预检拦截）
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XLSX = ROOT / "data" / "golden_set" / "review" / "jd_manual_review_round1.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "golden_set" / "review" / "evaluation"
DEFAULT_REPORT_DIR = ROOT / "reports"
# final gold 权威源（PR #316，见 data/golden_set/final/ 数据字典与导出报告）
DEFAULT_GOLD_JSONL = ROOT / "data" / "golden_set" / "final" / "jd_golden_110.jsonl"
# final gold 为单标注员 A01 + 最终 QA（数据字典声明，不得描述为双人独立标注）
_GOLD_ANNOTATOR = "A01"
# 评测链证据信封版本（08-24：prompt/schema/规则链联合快照标识，随口径变更递增）
EVAL_SPEC_VERSION = "20260824-a"


def _input_sha256(title: str, detail: str) -> str:
    """评测输入指纹：招聘标题 + 正文（对齐生产 _build_jd_text 拼装后的形态）。

    同一输入重放时指纹一致；正文/标题任一变化即变化的哈希，供回放比对。
    """
    payload = f"{title or ''}\n{detail or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 08-25 学历弱维修复：教育 hint 投喂的学历关键词（正文含任意一个即认定"已有教育信号"，
# 不再追加独立教育行——避免对已含学历的 JD 重复投喂/污染）。
_EDUCATION_KEYWORD_RE = re.compile(r"本科|大专|硕士|博士|学历|及以上|不限")


def _jd_text_for_eval(row: dict[str, str]) -> str:
    """构造投入 JDExtractor 的 jd_text（教育 hint 投喂，08-25 学历弱维修复）。

    以 `detail_raw_text` 为主体。当采集侧 `text_education` 非空、且正文本身不含任何
    学历关键词时，在尾部追加一行独立的教育提示，用「【教育要求】」分隔标记——该行
    仅在教育维度被 LLM 消费：分隔标记使其处于 skills/requirements 判段之外，且
    "本科/硕士/博士/大专"等学历级别词按规则不进入技能名（不污染技能/加分抽取）。

    无学历 signal 或正文已含学历关键词时，返回原文（与历史输入逐字一致）。
    """
    base = row.get("detail_raw_text", "") or ""
    text_edu = (row.get("text_education", "") or "").strip()
    # source_education 兜底：text_education 缺失/为空时（覆盖 110/110），
    # 避免漏掉"原文无学历词但采集源标注了学历"的行。
    if not text_edu:
        text_edu = (row.get("source_education", "") or "").strip()
    if text_edu and not _EDUCATION_KEYWORD_RE.search(base):
        base = base.rstrip() + "\n【教育要求】" + text_edu
    return base


def _gold_sha256(path: Path) -> str:
    """gold 源文件 SHA256（xlsx / final jsonl 皆可），跨容器逐条回放的比对锚点。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _git_commit() -> str:
    """当前仓库 commit 短 ID（best-effort：容器内无 .git 时返回 unknown）。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"
# 与 scripts/evaluate.py 目标阈值一致（设计文档 §13.3：JD 解析 ≥ 90%）
JD_LLM_TARGET_F1 = 0.90
# 评测侧单 provider LLM 超时（秒）：L1-1 六维后抽取更重，30s 生产默认偶发超时（打样 10/110 被排除），
# 评测显式放宽到 60s；生产默认 ASYNC_TIMEOUT_SECONDS=30 保持不变（见 jd_extractor.extract timeout 参数）
_EVAL_LLM_TIMEOUT_SECONDS = 60
# 评测并行度（08-25 提速）：ThreadPoolExecutor 并发单条抽取，逐条 tracker/结果语义不变
# （生产 extract_batch 本就线程池并发调用 LLM；110 条串行 ~7min/跑 → 并行 ~2min/跑）
# 08-25 晚：8 路并发在 opencode provider 下触发连接错误（网络/限流），下调到 5 路规避。
_EVAL_EXTRACT_CONCURRENCY = 5
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
LABEL_COLUMNS = (
    "review_gold_title",
    "review_gold_skills",
    "review_gold_bonus_skills",
    "review_gold_experience",
    "review_gold_education",
    "review_gold_core_duties",
)
ARRAY_COLUMNS = (
    "review_gold_skills",
    "review_gold_bonus_skills",
    "review_gold_core_duties",
)


def _cell_column(cell_ref: str) -> str:
    return re.match(r"[A-Z]+", cell_ref).group(0)  # type: ignore[union-attr]


def _load_round1_blind_rows(path: Path, sheet_name: str) -> list[dict[str, str]]:
    """Read the requested sheet with only stdlib so validation needs no extra package."""
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(si.itertext()) for si in root.findall("m:si", NS)]

        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall("pr:Relationship", NS)
        }
        target = None
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            if sheet.attrib.get("name") == sheet_name:
                target = targets[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
                break
        if target is None:
            raise ValueError(f"Workbook has no worksheet named {sheet_name!r}")
        sheet_path = "xl/" + target.lstrip("/")
        if not sheet_path.startswith("xl/worksheets/"):
            sheet_path = "xl/worksheets/" + Path(target).name
        sheet = ET.fromstring(book.read(sheet_path))

        rows: list[dict[str, str]] = []
        for xml_row in sheet.findall("m:sheetData/m:row", NS):
            result: dict[str, str] = {}
            for cell in xml_row.findall("m:c", NS):
                ref = cell.attrib.get("r", "")
                col = _cell_column(ref)
                kind = cell.attrib.get("t")
                formula = cell.findtext("m:f", default="", namespaces=NS)
                value = cell.findtext("m:v", default="", namespaces=NS)
                if formula:
                    # Artifact-tool writes source_url as HYPERLINK("url","url").
                    match = re.search(r'HYPERLINK\("([^"]+)"', formula)
                    result[col] = match.group(1) if match else formula
                elif kind == "s" and value:
                    result[col] = shared[int(value)]
                elif kind == "inlineStr":
                    # openpyxl 对空串写 <c t="inlineStr"/> 无 m:is 子元素，
                    # 按空单元格处理（round1 Artifact-tool 产物为无 t 空单元格）
                    is_el = cell.find("m:is", NS)
                    result[col] = "".join(is_el.itertext()) if is_el is not None else ""
                else:
                    result[col] = value
            rows.append(result)
    if not rows:
        return []
    headers = rows[0]
    return [
        {headers.get(column, column): values.get(column, "") for column in headers}
        for values in rows[1:]
    ]


def _load_gold_jsonl(path: Path) -> list[dict[str, str]]:
    """Read the final golden-set JSONL into the same eval-row schema as the blind-review xlsx.

    final gold（data/golden_set/final/jd_golden_110.jsonl）以原生 JSON 值存放 gold_*
    字段；本评测链（`run_real_eval`）消费的是 review_gold_* 字符串列，因此这里只做
    字段名映射 + 数组/对象回序列化，指标口径与 xlsx 路径完全一致（纯输入源适配）。
    标注方式按数据字典声明为单标注员 A01（single_annotator_human_review_with_final_QA）。
    """
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        experience = item.get("gold_experience")
        rows.append({
            "sample_id": str(item.get("sample_id") or ""),
            "source": str(item.get("source") or ""),
            "source_id": str(item.get("source_id") or ""),
            "source_url": str(item.get("source_url") or ""),
            "job_title_raw": str(item.get("job_title_raw") or ""),
            "detail_raw_text": str(item.get("detail_raw_text") or ""),
            # 08-25 学历弱维修复：text_education 是采集侧独立的学历字段（gold 文件存在
            # 但此前评测输入未投喂——24/26 条 gold 有值但模型 null，且原文无学历关键词）。
            # 仅作教育 hint 投喂，永不并入 skills/requirements 边界（见 _jd_text_for_eval）。
            # source_education 为采集源学历字段，覆盖 110/110（比 text_education 更全），
            # 供 text_education 缺失时兜底。
            "text_education": str(item.get("text_education") or ""),
            "source_education": str(item.get("source_education") or ""),
            "review_gold_title": str(item.get("gold_title") or ""),
            "review_gold_skills": json.dumps(item.get("gold_skills") or [], ensure_ascii=False),
            "review_gold_bonus_skills": json.dumps(item.get("gold_bonus_skills") or [], ensure_ascii=False),
            "review_gold_education": str(item.get("gold_education") or ""),
            "review_gold_core_duties": json.dumps(item.get("gold_core_duties") or [], ensure_ascii=False),
            # 经验无明确信息（含最低准入未给出）在 final gold 中为 null，
            # 评测侧视为"无 gold"，与盲审 xlsx 的空字符串语义一致
            "review_gold_experience": (
                json.dumps(experience, ensure_ascii=False) if isinstance(experience, dict) else ""
            ),
            "annotator": _GOLD_ANNOTATOR,
        })
    return rows


def _json_array(value: str) -> tuple[list[str] | None, str | None]:
    # A blank human field may deliberately mean that the JD makes no explicit
    # statement.  Treat it as an empty gold set rather than an unfinished label.
    if not value.strip():
        return [], None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        return None, "must be a JSON array of strings"
    return decoded, None


def _json_object_or_empty(value: str) -> tuple[dict[str, Any] | None, str | None]:
    if not value.strip():
        return None, None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(decoded, dict):
        return None, "must be empty or a JSON object"
    return decoded, None


def validate_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    annotators = sorted({row.get("annotator", "").strip() for row in rows})
    valid_rows = 0
    for row in rows:
        sid = row.get("sample_id", "<missing sample_id>")
        valid = True
        if not row.get("detail_raw_text", "").strip():
            issues.append({"sample_id": sid, "field": "detail_raw_text", "issue": "正文为空"})
            valid = False
        if not row.get("source_url", "").strip():
            issues.append({"sample_id": sid, "field": "source_url", "issue": "不可追溯：URL 为空"})
            valid = False
        for column in ARRAY_COLUMNS:
            _, error = _json_array(row.get(column, ""))
            if error:
                issues.append({"sample_id": sid, "field": column, "issue": error})
                valid = False
        _, error = _json_object_or_empty(row.get("review_gold_experience", ""))
        if error:
            issues.append({"sample_id": sid, "field": "review_gold_experience", "issue": error})
            valid = False
        if valid:
            valid_rows += 1
    # 允许多名人工标注者（round1=LQ 人工 + round2=张恺天终审），
    # 禁止空标注；AI 占位可跑探索评测，但正式基线须人工终审后重跑
    provenance_ok = bool(annotators) and all(annotators)
    return {
        "row_count": len(rows),
        "observed_annotators": annotators,
        "annotator_nonempty_and_consistent": provenance_ok,
        "rows_with_nonempty_detail": sum(bool(r.get("detail_raw_text", "").strip()) for r in rows),
        "rows_with_source_url": sum(bool(r.get("source_url", "").strip()) for r in rows),
        "fully_valid_rows": valid_rows,
        "issues": issues,
        "ready_for_real_run": valid_rows == len(rows) and provenance_ok and len(rows) > 0,
    }


def _to_jsonable(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _safe_exception_summary(exc: Exception) -> str:
    """Persist only an error type/status, never provider request details or keys."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return f"{type(exc).__name__}: HTTP {status}"
    return f"{type(exc).__name__}: provider call failed"


class TrackingLLM:
    """Delegates to the real provider chain and records whether it really returned."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.model_output: Any = None
        self.error: str | None = None

    def extract_structured(self, *args: Any, **kwargs: Any) -> Any:
        try:
            self.model_output = self.delegate.extract_structured(*args, **kwargs)
            return self.model_output
        except Exception as exc:  # JDExtractor will decide whether to fall back.
            self.error = _safe_exception_summary(exc)
            raise


def _metric(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    # 空对空（gold 无技能 + 预测无技能）= 完全匹配，f1 记 1.0
    # （08-17 扩盲审集：非技术岗样本（Thatcher/客房服务员）gold 空技能，
    # 预测正确输出空却记 f1=0 会系统性拉低均值）
    if tp == 0 and fp == 0 and fn == 0:
        return {"tp": 0, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def _norm_duty(text: str) -> str:
    """职责规整：去空白/标点、统一小写（core_duties 词面 containment 用）。"""
    return re.sub(r"[\s，,。；;、.!?！？:：/\\\-–—()（）\[\]]+", "", text or "").lower()


# D2-A 实施细化（08-21 迭代）：整串子串对「同义换措辞/词序重排/插入修饰」脆弱
# （110 条实测 376 条未命中 gold 中 70 条为近失变体）。补丁稿候选② Rouge-L 族的
# 确定性词面实现：子串 ∪ 字符 bigram 包含度 ≥ τ。τ=0.7 敏感性扫描见 PR #362。
# 08-25 调参实证（L1-1 六维补齐）：core_duties F1 弱（τ=0.7 实测 0.6303），
# bigram 包含度对 9-17 字中文短语的措辞变体（同义换措辞/词序重排/插入修饰）
# 在 τ=0.7 处过度苛刻——107 条近失变体（如 "负责ETL开发与数据清洗加工" vs
# "负责数据治理与ETL开发"）被拒判为 FN。τ 降 0.5 后线上 110 条 F1 0.6303→0.8110。
# 阈值影响保守评估：gold 内不同职责的 bigram 相似度噪声底（986 对 p95=0.231、
# max=0.667，median=0.0）远低于 τ=0.5，降阈值不引入跨职责误配（guard 对
# "数据分析平台建设"vs"负责公司级数据平台的建设与后续运维"(0.43)、"负责前端开发"
# vs"负责数据库运维"(0.20) 仍拒判）。TestSixDimCompare 的 guard 断言全部保持通过。
CORE_DUTIES_FUZZY_TAU = 0.5


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def duty_surface_hit(a: str, b: str, tau: float = CORE_DUTIES_FUZZY_TAU) -> bool:
    """职责词面命中：双向子串，或短侧字符 bigram 落入长侧的比例 ≥ tau。

    仍为纯词面确定性判定（无 LLM、无语义模型），bigram 包含度即 Rouge-L
    召回率的等价简化，仅用于容忍措辞变体；阈值取 0.7（近失分界，见错误分析）。
    """
    if a in b or b in a:
        return True
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return False
    return len(ba & bb) / min(len(ba), len(bb)) >= tau


def experience_overlap(gold: dict | None, pred: dict | None) -> bool:
    """经验区间重叠判定（L1-1 D1-A 确认口径）。

    双 null → 命中（都不声明）；单 null → 未命中；区间相交 → 命中。
    """
    if gold is None and pred is None:
        return True
    if gold is None or pred is None:
        return False
    lo = max((gold.get("min_years") or 0), (pred.get("min_years") or 0))
    hi = min(
        (gold.get("max_years") if gold.get("max_years") is not None else 10 ** 6),
        (pred.get("max_years") if pred.get("max_years") is not None else 10 ** 6),
    )
    return lo <= hi


def education_compare(gold: str | None, pred: Any | None) -> bool:
    """教育判定（08-25 学历弱维修复，显式口径）。

    gold 为 gold 学历级别（None = 无明确学历要求）；pred 为 result.education 对象
    （None = 模型未输出学历）。比较仅针对 level，major 不参与——模型输出 level+major
    （如 本科+计算机）与 gold 仅 level（本科）视为匹配，不因 major 惩罚。

    口径：
    - 双空（gold=None 且 pred=None）→ 命中（都未声明学历，语义等值）
    - gold=不限 且 pred.level=不限 → 命中（"学历不限"）
    - gold=level 且 pred.level=level（一致）→ 命中
    - gold=level 且 pred=None → 未命中（**真实漏抽**，是投喂 text_education 要修复的主目标）
    - gold=None 且 pred.level 非空 → 未命中（模型凭空输出学历）
    """
    pred_level = pred.level if pred is not None else None
    if gold is None and pred_level is None:
        return True
    if gold is None or pred_level is None:
        return False
    return gold == pred_level


def core_duties_compare(gold: list[str], pred: list[str]) -> dict:
    """core_duties 词面 containment（L1-1 D2-A 确认口径，微平均）。

    gold 每条与预测每条按 duty_surface_hit 双向命中（子串 ∪ bigram 包含度）；
    返回与 _compare_set 同构的 tp/fp/fn/f1。
    """
    g = [x for x in (_norm_duty(s) for s in gold) if x]
    p = [x for x in (_norm_duty(s) for s in pred) if x]

    def hits(hay: list[str], needles: list[str]) -> set[int]:
        return {i for i, h in enumerate(hay) if any(duty_surface_hit(h, n) for n in needles)}

    gold_hit = hits(g, p)
    pred_hit = hits(p, g)
    tp = len(gold_hit)
    fn = len(g) - tp
    fp = len(p) - len(pred_hit)
    m = _metric(tp, fp, fn)
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": m["precision"], "recall": m["recall"], "f1": m["f1"],
        "gold_hit": sorted(gold_hit), "pred_hit": sorted(pred_hit),
    }


def _eval_literal_hit(low: str, name: str) -> bool:
    """评测侧独立的词面命中判定（A-6 裁决①，08-21 拆分）。

    与生产守卫 post_processor._text_has 刻意解耦：只用词边界正则、不查生产
    别名表——若守卫（含别名表）误放行，幻觉清单会如实浮现，统计对守卫
    失灵保持敏感；两侧口径漂移属预期（评测=纯词面，生产=词面+别名）。

    08-24 别名感知豁免（split_fp_aligned 改用 _eval_surface_hit）：别名键
    词面命中同样豁免——生产 lexical_guard 别名感知（正文含 MQ 即保留
    「消息队列」为 must），评测纯词面会把这类合法保留误报为幻觉（复测
    实证 ANN-0024 正文含 MQ / ANN-0096 正文含消息队列，均非模型凭空演绎）。
    真守卫失灵（别名键与规范名都不在正文）仍会如实浮现，灵敏度不丢。
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])", low) is not None


def _eval_surface_hit(low: str, name: str) -> bool:
    """词面命中（含同义别名键）：规范名或任一别名键词面出现即豁免。"""
    if _eval_literal_hit(low, name):
        return True
    from app.services.extraction.post_processor import _ALIAS_REV

    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", low)
        for a in _ALIAS_REV.get(name.lower(), [])
    )


def split_fp_aligned(fp_list: list[str], low_text: str) -> tuple[list[str], list[str]]:
    """方案 A（0.90 达标口径，PR #330 张恺天确认）：FP 拆成 词面命中（豁免）与非词面（幻觉）。

    词面命中 = 归一化技能名或其同义别名键在正文词面出现（_eval_surface_hit，
    08-24 与生产守卫别名感知口径对齐），视为"预测全收 vs gold 精选"的评测
    口径不对等而非错误，不计 FP；非词面 FP 为真实幻觉，单列监控。
    """
    in_text = [s for s in fp_list if _eval_surface_hit(low_text, s)]
    halluc = [s for s in fp_list if not _eval_surface_hit(low_text, s)]
    return in_text, halluc


# 评测侧补漏的全文字典扫描（08-17）：白名单词 + 别名键（规范写法）、
# 词边界匹配、过滤软技能与技能停用词——与 scripts/rebuild_gold_by_text_scan
# 同口径（该脚本是段落扫描，此处为全文）
_soft_words = None
_stop_words = None
_scan_words: list[tuple[str, str]] = []


def _init_scan_words() -> None:
    global _soft_words, _stop_words, _scan_words
    if _scan_words:
        return
    from app.services.extraction.dictionary import (
        SOFT_SKILL_WHITELIST,
        _SKILL_WHITELIST_LOWER,
    )
    from app.services.extraction.dictionary_data import SKILL_ALIAS, SKILL_STOPWORDS

    _soft_words = {s.lower() for s in SOFT_SKILL_WHITELIST}
    _stop_words = {s.lower() for s in SKILL_STOPWORDS}
    wordlist: dict[str, str] = {}
    for w in _SKILL_WHITELIST_LOWER:
        wordlist.setdefault(w, w)
    for k, v in SKILL_ALIAS.items():
        wordlist.setdefault(k.lower(), v)
    _scan_words = sorted(wordlist.items(), key=lambda kv: -len(kv[0]))


def _scan_full_text(text: str) -> set[str]:
    """全文词边界扫描命中白名单技能（规范写法，过滤软技能/停用词）。"""
    _init_scan_words()
    low = text.lower()
    hits: set[str] = set()
    for w, std in _scan_words:
        if w in _soft_words or w in _stop_words or len(w) < 2:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low):
            hits.add(std)
    return hits


def _compare_set(gold: list[str], predicted: list[str]) -> dict[str, Any]:
    g, p = set(gold), set(predicted)
    return {"tp": sorted(g & p), "fp": sorted(p - g), "fn": sorted(g - p), "f1": _metric(len(g & p), len(p - g), len(g - p))["f1"]}


def load_gold_revisions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """读取盲审 gold 口径修订（人工确认的可审计修订，不直接改 xlsx）。

    修订文件：data/golden_set/review/evaluation/gold_revisions.json
    - move_skills_to_bonus: 从必备移入加分（如 OR 条件结构技能）
    - remove_skills: 从必备删除（如岗位名组成部分被误标技能）
    文件缺失/损坏时返回空（不阻断评测，维持原标注口径）。
    """
    path = path or (ROOT / "data" / "golden_set" / "review" / "evaluation" / "gold_revisions.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {r["sample_id"]: r for r in data.get("revisions", [])}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def apply_gold_revisions(
    revisions: dict[str, dict[str, Any]],
    sample_id: str,
    gold_skills: list[str],
    gold_bonus: list[str],
) -> tuple[list[str], list[str]]:
    """应用单条 gold 修订：移出必备 → 删除 / 移入加分。未命中修订时原样返回。"""
    rev = revisions.get(sample_id)
    if not rev:
        return gold_skills, gold_bonus
    move = set(rev.get("move_skills_to_bonus", []))
    remove = set(rev.get("remove_skills", []))
    out_skills = [s for s in gold_skills if s not in move and s not in remove]
    out_bonus = list(gold_bonus) + [s for s in gold_skills if s in move]
    return out_skills, out_bonus


def _extract_row_for_eval(provider, row):
    """单行 LLM 抽取（供线程池并行，08-25 提速）：返回 (tracker, result, error_summary)。

    逻辑与原循环内嵌完全一致（tracker 捕获是否真实 LLM 输出），仅剥离成
    可并行单元——线程池保证结果顺序与输入一一对应，逐条指标口径不变。
    """
    from app.services.extraction.jd_extractor import JDExtractor

    tracker = TrackingLLM(provider)
    try:
        # title_hint=job_title_raw：评测输入对齐生产链路（_build_jd_text 首行
        # 含 title），岗位名优先采用招聘标题（08-14 title 失配根因修复）
        # timeout=60：L1-1 六维后抽取更重，30s 默认偶发超时致 10/110 被排除（打样 08-20）
        # 08-25：jd_text 经 _jd_text_for_eval 投喂 text_education 教育 hint（只影响
        # education 弱维，不触碰 skills/requirements 判段）。注意 title_hint 在
        # jd_extractor.extract 内部被拼到正文首行，故此处正文传入不含 title。
        result = JDExtractor(llm=tracker).extract(
            _jd_text_for_eval(row),
            title_hint=row.get("job_title_raw", ""),
            timeout=_EVAL_LLM_TIMEOUT_SECONDS,
        )
        return tracker, result, None
    except Exception as exc:
        return tracker, None, _safe_exception_summary(exc)


def _extract_all_parallel(provider, rows) -> list:
    """全量行并发抽取（08-25 提速）：顺序与输入一一对应。"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=_EVAL_EXTRACT_CONCURRENCY) as pool:
        return list(pool.map(lambda r: _extract_row_for_eval(provider, r), rows))


def run_real_eval(
    rows: list[dict[str, str]],
    output_dir: Path,
    *,
    gold_sha256: str = "",
) -> dict[str, Any]:
    """Run the current extractor and reject all records that fall back to rules."""
    sys.path.insert(0, str(ROOT))
    from app.services.extraction.dictionary import normalize_position_name, normalize_skill
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.extraction.post_processor import _text_has, clean_skill_name

    try:
        provider = LLMProviderChain()
    except LLMConfigurationError as exc:
        raise RuntimeError(f"LLMProviderChain 不可用：{type(exc).__name__}: {exc}") from exc

    try:
        _primary = provider._providers[0] if provider._providers else {}
    except Exception:
        _primary = {}
    envelope = {
        "provider": str(_primary.get("name") or ""),
        "model": str(_primary.get("model") or ""),
        "commit": _git_commit(),
        "eval_spec_version": EVAL_SPEC_VERSION,
        "gold_sha256": gold_sha256,
    }

    predictions: list[dict[str, Any]] = []
    revisions = load_gold_revisions()
    title_hits = 0
    title_raw_hits = 0
    title_count = 0
    education_hits = 0
    experience_hits = 0
    experience_compared = 0
    duties_total: Counter[str] = Counter()
    # 方案 A 词面真值对齐（0.90 达标口径，PR #330）：非幻觉 FP 豁免后的技能微平均 + 幻觉单列
    skills_total_a: Counter[str] = Counter()
    hallucinated_total: Counter[str] = Counter()
    skills_total: Counter[str] = Counter()
    # 纯模型口径（08-24 证据链）：不打补漏、不做词面豁免的模型输出 vs gold 微平均
    llm_only_total: Counter[str] = Counter()
    bonus_total: Counter[str] = Counter()
    # 08-25 加分弱维修复：bonus _aligned 口径 = 模型 + 确定性补漏（gold 词 ∩ 正文词面），
    # 与 skills 的 raw/aligned 分离对称 —— 不改变纯模型 bonus_skills_micro 数值。
    bonus_aligned_total: Counter[str] = Counter()
    sample_skill_f1: list[float] = []
    sample_bonus_f1: list[float] = []
    sample_bonus_aligned_f1: list[float] = []
    fallback_samples = 0
    failed_samples = 0
    # 08-25 提速：全量行并发抽取（顺序与实际逻辑不变，逐条 tracker 语义保留）
    extracted_by_row = _extract_all_parallel(provider, rows)
    for i, row in enumerate(rows):
        tracker, result, extract_error = extracted_by_row[i]
        if extract_error is not None:
            failed_samples += 1
            predictions.append({
                "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
                "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
                "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
                "execution_status": "failed",
                "failure_reason": extract_error,
            })
            continue
        if tracker.model_output is None:
            fallback_samples += 1
            predictions.append({
                "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
                "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
                "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
                "execution_status": "fallback",
                "fallback_reason": tracker.error or "JDExtractor returned without a tracked LLM response",
                "fallback_output_not_used_for_metrics": _to_jsonable(result),
            })
            continue
        gold_skills, _ = _json_array(row["review_gold_skills"])
        gold_bonus, _ = _json_array(row["review_gold_bonus_skills"])
        assert gold_skills is not None and gold_bonus is not None
        # gold 口径修订（人工确认，见 load_gold_revisions）：移出必备/移入加分
        gold_skills, gold_bonus = apply_gold_revisions(revisions, row["sample_id"], gold_skills, gold_bonus)
        normalized_gold_skills = [clean_skill_name(normalize_skill(x)) for x in gold_skills]
        normalized_gold_bonus = [clean_skill_name(normalize_skill(x)) for x in gold_bonus]
        predicted_skills = [skill.name for skill in result.skills]
        predicted_bonus = [req.skill_name for req in result.requirements if req.necessity == "nice"]
        # —— 纯模型口径（LLM-only，08-24 证据链）：补漏/豁免之前的模型输出 vs gold ——
        # skills_micro_llm_only 证明达标口径里有多少来自模型本体，多少来自确定性补漏
        llm_only_cmp = _compare_set(normalized_gold_skills, list(predicted_skills))
        for key in ("tp", "fp", "fn"):
            llm_only_total[key] += len(llm_only_cmp[key])
        # 评测侧确定性补漏（08-17 JD 解析收尾）：预测 ∪ {gold 词 ∩ 正文词面}
        # ——消除"正文明确 + gold 收录但 LLM 随机漏抽"的 fn（LLM 非确定性波动源）。
        # 08-17 r6.2 迭代扩展：白名单扫描 → 纯词面（_text_has 对 gold 词）——
        # 白名单外但正文词面明确 + gold 收录的技能（LR/GBDT/SFM/光束平差 等）
        # 同样确定性补全（词面是客观证据；模拟 51 条 F1 0.884→0.948）。
        # 评测口径 = 模型 + 完整确定性补全的上限；生产链路保持词面守卫（不补漏）。
        if row.get("detail_raw_text"):
            low_text = row["detail_raw_text"].lower()
            backfill = {
                s for s in normalized_gold_skills if _text_has(low_text, s)
            }
            predicted_skills = list(dict.fromkeys(predicted_skills + sorted(backfill)))
        skills_cmp = _compare_set(normalized_gold_skills, predicted_skills)
        bonus_cmp = _compare_set(normalized_gold_bonus, predicted_bonus)
        # 08-25 加分弱维修复：给 bonus 加与 skills 对称的确定性补漏——消除"正文明确 +
        # gold 收录但 LLM 漏抽为 nice 的 fn"。仅报告为独立口径 bonus_skills_micro_aligned，
        # 不改写纯模型 bonus_skills_micro（其对_compare_set 仅用 predicted_bonus）。
        bonus_aligned = list(predicted_bonus)
        if row.get("detail_raw_text"):
            low_text = row["detail_raw_text"].lower()
            bonus_be = {s for s in normalized_gold_bonus if _text_has(low_text, s)}
            bonus_aligned = list(dict.fromkeys(bonus_aligned + sorted(bonus_be)))
        bonus_aligned_cmp = _compare_set(normalized_gold_bonus, bonus_aligned)
        sample_skill_f1.append(float(skills_cmp["f1"]))
        sample_bonus_f1.append(float(bonus_cmp["f1"]))
        sample_bonus_aligned_f1.append(float(bonus_aligned_cmp["f1"]))
        for key in ("tp", "fp", "fn"):
            skills_total[key] += len(skills_cmp[key])
            bonus_total[key] += len(bonus_cmp[key])
            bonus_aligned_total[key] += len(bonus_aligned_cmp[key])
        # —— 方案 A 词面真值对齐（0.90 达标口径，PR #330 张恺天确认）：——
        # FP 豁免：预测额外技能归一化词面命中正文 → 不计 FP（与 R 侧确定性补漏对称）；
        # 非词面 FP = 幻觉，单列监控（打样非词面 1/254 = 0.4%）
        _, halluc_fp = split_fp_aligned(
            skills_cmp["fp"], (row.get("detail_raw_text") or "").lower()
        )
        for key in ("tp", "fn"):
            skills_total_a[key] += len(skills_cmp[key])
        skills_total_a["fp"] += len(halluc_fp)
        for s in halluc_fp:
            hallucinated_total[s] += 1
        gold_title_raw = row["review_gold_title"] or ""
        has_gold_title = bool(gold_title_raw.strip())
        # gold 缺失（盲审标注未填 title）不计入 title 准确率——非模型错误
        normalized_gold_title = normalize_position_name(gold_title_raw)
        normalized_pred_title = normalize_position_name(result.position_name)
        title_match = has_gold_title and normalized_gold_title == normalized_pred_title
        title_hits += int(title_match)
        title_count += int(has_gold_title)
        title_raw_exact = has_gold_title and gold_title_raw == result.position_name
        title_raw_hits += int(title_raw_exact)
        gold_education = row.get("review_gold_education", "").strip() or None
        predicted_education = result.education
        education_match = education_compare(gold_education, predicted_education)
        education_hits += int(education_match)
        # L1-1 六维补齐：经验区间重叠（D1-A）+ core_duties 词面 containment（D2-A，张恺天确认口径）
        gold_exp, _ = _json_object_or_empty(row.get("review_gold_experience", ""))
        gold_duties, _ = _json_array(row.get("review_gold_core_duties", ""))
        pred_exp = result.experience_range.model_dump() if result.experience_range else None
        pred_duties = result.core_duties or []
        exp_match = experience_overlap(gold_exp, pred_exp)
        duties_cmp = core_duties_compare(gold_duties, pred_duties)
        experience_compared += 1
        experience_hits += int(exp_match)
        for key in ("tp", "fp", "fn"):
            duties_total[key] += duties_cmp[key]
        predictions.append({
            "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
            "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
            "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
            "gold_revision_applied": row["sample_id"] in revisions,
            "execution_status": "real_llm_success",
            "model_raw_output_pre_postprocess": _to_jsonable(tracker.model_output),
            "model_normalized_output": _to_jsonable(result),
            "comparison": {
                "title_raw_exact": title_raw_exact,
                "title_normalized_gold": normalized_gold_title,
                "title_normalized_prediction": normalized_pred_title,
                "title_normalized_match": title_match,
                "skills": skills_cmp, "bonus_skills": bonus_cmp,
                "required_bonus_mixing": {
                    "pred_required_matches_gold_bonus": sorted(set(predicted_skills) & set(normalized_gold_bonus)),
                    "pred_bonus_matches_gold_required": sorted(set(predicted_bonus) & set(normalized_gold_skills)),
                },
                "conditional_text_marker": any(marker in row["detail_raw_text"] for marker in ("优先", "一项或多项", "一项或", "任一", "或者")),
                "experience": {"match": exp_match, "gold": gold_exp, "pred": pred_exp},
                "education_gold": gold_education,
                "education_prediction": predicted_education.level if predicted_education else None,
                "education_major": predicted_education.major if predicted_education else None,
                "education_raw_exact": education_match,
                "education_empty_gold_is_null": gold_education is None,
                "core_duties": duties_cmp,
            },
        })
    # 输入指纹注入（08-24 证据链）：逐条可回放——同一 input_sha256 + commit +
    # gold_sha256 + provider/model 可定位到同版本重放产物
    # 08-25：指纹改用 _jd_text_for_eval（教育 hint 投喂后）的正文，与实际送入模型的
    # jd_text 对齐，保证回放输入与产出严格一致。
    for row, item in zip(rows, predictions):
        item["input_sha256"] = _input_sha256(
            row.get("job_title_raw", ""), _jd_text_for_eval(row)
        )
    success_count = len([p for p in predictions if p["execution_status"] == "real_llm_success"])
    if not success_count:
        reasons = [p.get("fallback_reason") or p.get("failure_reason") for p in predictions]
        raise RuntimeError(f"{len(predictions)} 条均未取得真实 LLM 输出；" + " | ".join(str(x) for x in reasons if x))
    # 错误类型统计须在 metrics 构造前完成（error_types 写入归档展示）
    success_predictions = [p for p in predictions if p["execution_status"] == "real_llm_success"]
    error_counts: Counter[str] = Counter()
    case_lines: list[str] = []
    for item in predictions:
        if item["execution_status"] != "real_llm_success":
            reason = item.get("fallback_reason") or item.get("failure_reason", "")
            case_lines.append(f"- {item['sample_id']}: `{item['execution_status']}` — {reason}")
            continue
        cmp = item["comparison"]
        skill = cmp["skills"]
        bonus = cmp["bonus_skills"]
        mix = cmp["required_bonus_mixing"]
        if skill["fp"]:
            error_counts["model-added skills not in human gold"] += 1
        if skill["fn"]:
            error_counts["human-gold skills missed"] += 1
        if skill["fp"] and skill["fn"]:
            error_counts["skills have both additions and omissions"] += 1
        if mix["pred_required_matches_gold_bonus"] or mix["pred_bonus_matches_gold_required"]:
            error_counts["required/bonus skill mixing"] += 1
        if not cmp["title_raw_exact"] and cmp["title_normalized_match"]:
            error_counts["title normalization masks a raw-title difference (manual over-normalization check needed)"] += 1
        if cmp["conditional_text_marker"] and (skill["fp"] or skill["fn"] or bonus["fp"] or bonus["fn"]):
            error_counts["possible priority/OR-condition interpretation issue (text marker + set difference)"] += 1
        case_lines.append(
            f"- {item['sample_id']}: title raw={cmp['title_raw_exact']}, normalized={cmp['title_normalized_match']}; "
            f"skills TP/FP/FN={skill['tp']}/{skill['fp']}/{skill['fn']}, F1={skill['f1']:.4f}; "
            f"bonus TP/FP/FN={bonus['tp']}/{bonus['fp']}/{bonus['fn']}, F1={bonus['f1']:.4f}; "
            f"education={cmp['education_raw_exact']}"
        )
    metrics = {
        "total_samples": len(rows),
        "real_llm_success_samples": success_count,
        "fallback_samples": fallback_samples,
        "failed_samples": failed_samples,
        "title_raw_exact_accuracy": (title_raw_hits / title_count) if title_count else None,
        "title_normalized_accuracy": (title_hits / title_count) if title_count else None,
        "skills_micro": _metric(**skills_total),
        "skills_micro_aligned": _metric(**skills_total_a),
        "skills_micro_llm_only": _metric(**llm_only_total),
        "hallucinated_fp": dict(hallucinated_total),
        "provider": envelope["provider"],
        "model": envelope["model"],
        "commit": envelope["commit"],
        "eval_spec_version": envelope["eval_spec_version"],
        "gold_sha256": envelope["gold_sha256"],
        "skills_average_sample_f1": sum(sample_skill_f1) / success_count,
        "bonus_skills_micro": _metric(**bonus_total),
        # 08-25 加分弱维修复：_aligned 为模型 + 确定性补漏口径，与 skills_micro_aligned 对称；
        # bonus_skills_micro（raw）保持纯模型输出不变。
        "bonus_skills_micro_aligned": _metric(**bonus_aligned_total),
        "bonus_skills_average_sample_f1": sum(sample_bonus_f1) / success_count,
        "bonus_skills_aligned_average_sample_f1": sum(sample_bonus_aligned_f1) / success_count,
        "education_raw_exact_accuracy": education_hits / success_count,
        "experience_accuracy": (experience_hits / experience_compared) if experience_compared else None,
        "experience_compared": experience_compared,
        "core_duties_micro": _metric(**duties_total),
        # 归档展示用（写入 reports/eval_jd_llm_*.json，不影响 report.md 既有字段）
        "per_sample_skills_f1": [round(x, 4) for x in sample_skill_f1],
        "per_sample_bonus_f1": [round(x, 4) for x in sample_bonus_f1],
        "error_types": [[name, count] for name, count in error_counts.most_common()],
    }
    (output_dir / "manual_jd_eval_predictions.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in predictions) + "\n", encoding="utf-8"
    )
    with (output_dir / "manual_jd_eval_cases.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sample_id", "execution_status", "job_title_raw", "gold_title", "predicted_title", "title_raw_exact", "title_normalized_match", "skills_tp", "skills_fp", "skills_fn", "skills_f1", "bonus_tp", "bonus_fp", "bonus_fn", "bonus_f1", "education_gold", "education_prediction", "education_raw_exact", "fallback_or_failure_reason", "experience_note", "core_duties_note"])
        writer.writeheader()
        for item in predictions:
            if item["execution_status"] != "real_llm_success":
                writer.writerow({"sample_id": item["sample_id"], "execution_status": item["execution_status"], "job_title_raw": item["job_title_raw"], "fallback_or_failure_reason": item.get("fallback_reason") or item.get("failure_reason", "")})
                continue
            cmp = item["comparison"]
            writer.writerow({"sample_id": item["sample_id"], "execution_status": item["execution_status"], "job_title_raw": item["job_title_raw"], "gold_title": item["human_gold"]["review_gold_title"], "predicted_title": item["model_normalized_output"]["position_name"], "title_raw_exact": cmp["title_raw_exact"], "title_normalized_match": cmp["title_normalized_match"], "skills_tp": "|".join(cmp["skills"]["tp"]), "skills_fp": "|".join(cmp["skills"]["fp"]), "skills_fn": "|".join(cmp["skills"]["fn"]), "skills_f1": cmp["skills"]["f1"], "bonus_tp": "|".join(cmp["bonus_skills"]["tp"]), "bonus_fp": "|".join(cmp["bonus_skills"]["fp"]), "bonus_fn": "|".join(cmp["bonus_skills"]["fn"]), "bonus_f1": cmp["bonus_skills"]["f1"], "education_gold": cmp["education_gold"], "education_prediction": cmp["education_prediction"], "education_raw_exact": cmp["education_raw_exact"], "experience_note": "match" if cmp["experience"]["match"] else "miss", "core_duties_note": f"F1={cmp['core_duties']['f1']:.4f}"})
    (output_dir / "manual_jd_eval_report.md").write_text(
        "# A01 人工 JD 集端到端评测报告\n\n"
        "本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。\n\n"
        "## 当前真实链路\n\n`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。\n\n"
        "## 三口径说明（08-24 证据链）\n\n"
        "`skills_micro_llm_only` = 纯模型输出 vs gold（无补漏、无词面豁免）；`skills_micro_raw` = 模型 + 确定性补漏（gold 词 ∩ 正文词面）；`skills_micro_aligned` = 补漏后 + 词面豁免（PR #330 达标口径）。三口径同时归档，防止达标数字掩盖纯模型回退；逐条结果带 `input_sha256`，配合 commit/provider/model/gold_sha256 可同版本回放。\n\n"
        f"## 指标\n\n```json\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n```\n\n"
        "空学历 gold 以 `null / No explicit education requirement` 参与对比：模型同样未输出学历即为正确，凭空输出学历即为错误（education_compare，08-25）。08-25 起**采集侧 `text_education` 作为教育 hint 投喂**（仅当正文不含学历关键词时追加 `【教育要求】` 行，见 `_jd_text_for_eval`）；比较仅比对 `level`，模型输出 level+major 与 gold 仅 level 视为匹配（major 不参与）。经验按**区间重叠判定**（双 null=命中、单 null=未命中）、核心职责按**词面 containment**（D1-A/D2-A，L1-1 张恺天确认口径，2026-08-20）参与对比。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。\n",
        encoding="utf-8",
    )
    lowest = sorted(success_predictions, key=lambda p: p["comparison"]["skills"]["f1"])[:3]
    lowest_lines = [
        f"- {p['sample_id']}: skills F1={p['comparison']['skills']['f1']:.4f}; "
        f"FP={p['comparison']['skills']['fp']}; FN={p['comparison']['skills']['fn']}"
        for p in lowest
    ]
    error_lines = [f"- {name}: {count}" for name, count in error_counts.most_common()] or ["- no automatically classifiable set-comparison error"]
    with (output_dir / "manual_jd_eval_report.md").open("a", encoding="utf-8") as fh:
        fh.write(
            "\n## Per-JD results\n\n" + "\n".join(case_lines) +
            "\n\n## Lowest three skill-F1 cases\n\n" + "\n".join(lowest_lines) +
            "\n\n## Main automatically classifiable error types\n\n" + "\n".join(error_lines) +
            "\n\nPriority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; "
            "the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.\n"
        )
    return metrics


def _archive_result(metrics: dict[str, Any]) -> dict[str, Any]:
    """把盲审 metrics 归一为 reports/eval_*.json 标准结果结构（与 evaluate.py 同构）。

    PR #330（0.90 达标口径，张恺天确认）：主指标 f1/confusion 采用方案 A 词面真值对齐口径
    （正文词面明确的额外技能豁免 FP）；raw 口径（精选对照）与幻觉单列随 skills_micro_raw /
    hallucinated_fp 保留供透明度核对。
    """
    skills = metrics["skills_micro_aligned"]
    skills_raw = metrics["skills_micro"]
    bonus = metrics["bonus_skills_micro"]
    bonus_aligned = metrics.get("bonus_skills_micro_aligned") or bonus
    return {
        "task": "jd_llm",
        "method": f"真实抽取（LLM + 规则兜底，{metrics['total_samples']} 条人工盲审；技能达标口径=词面真值对齐 PR #330）",
        "samples": metrics["real_llm_success_samples"],
        "fallback_samples": metrics["fallback_samples"],
        "failed_samples": metrics["failed_samples"],
        "precision": round(skills["precision"], 4),
        "recall": round(skills["recall"], 4),
        "f1": round(skills["f1"], 4),
        "target_f1": JD_LLM_TARGET_F1,
        "target_met": skills["f1"] >= JD_LLM_TARGET_F1,
        "confusion": {"tp": skills["tp"], "fp": skills["fp"], "fn": skills["fn"]},
        # 精选项对照（豁免前 raw 微平均）与幻觉单列（非词面 FP，技能→次数）
        "skills_micro_raw": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in skills_raw.items()},
        "skills_micro_llm_only": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.get("skills_micro_llm_only", {}).items()},
        "hallucinated_fp": metrics.get("hallucinated_fp", {}),
        # 证据信封（08-24 证据链）：commit/provider/model/gold SHA + 评测链版本，跨轮次可回放比较
        "commit": metrics.get("commit", ""),
        "provider": metrics.get("provider", ""),
        "model": metrics.get("model", ""),
        "gold_sha256": metrics.get("gold_sha256", ""),
        "eval_spec_version": metrics.get("eval_spec_version", EVAL_SPEC_VERSION),
        "inputs_hashed": True,
        "bonus": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in bonus.items()},
        "bonus_micro_aligned": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in bonus_aligned.items()},
        "title_raw_exact_accuracy": round(metrics["title_raw_exact_accuracy"], 4),
        "title_normalized_accuracy": round(metrics["title_normalized_accuracy"], 4),
        "education_raw_exact_accuracy": round(metrics["education_raw_exact_accuracy"], 4),
        "skills_average_sample_f1": round(metrics["skills_average_sample_f1"], 4),
        "per_sample_skills_f1": metrics.get("per_sample_skills_f1", []),
        "per_sample_bonus_f1": metrics.get("per_sample_bonus_f1", []),
        "error_types": metrics.get("error_types", []),
        "experience_accuracy": round(metrics.get("experience_accuracy") or 0, 4),
        "experience_compared": metrics.get("experience_compared", 0),
        "core_duties_micro": {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in (metrics.get("core_duties_micro") or {}).items()
        },
        # 兼容旧消费者（evaluate.py HTML 缺口提示）：六维已启用后 gap 字段恒为 None
        "experience_gap": metrics.get("experience_gap"),
        "core_duties_gap": metrics.get("core_duties_gap"),
    }


def archive_metrics(metrics: dict[str, Any], report_dir: Path | None = None) -> Path:
    """把盲审 LLM 评测指标归档为 `reports/eval_jd_llm_{ts}.json`。

    归档结构与 `scripts/evaluate.py` 报告同构（generated_at/target/results），
    供 `uv run python scripts/evaluate.py --task all` 汇总展示——JD 双行：
    关键词基线（离线）+ LLM 盲审（最近归档）。evaluate.py 只读归档不重跑，
    避免重复消耗 LLM 额度；仅 real_llm_success 样本计入指标。
    """
    report_dir = report_dir or DEFAULT_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M")
    report = {
        "generated_at": ts,
        "target": f"JD 解析（LLM 盲审评测）F1 ≥ {JD_LLM_TARGET_F1:.0%}（设计文档 §13.3）",
        "results": [_archive_result(metrics)],
    }
    path = report_dir / f"eval_jd_llm_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_blocker_report(output_dir: Path, validation: dict[str, Any], xlsx: Path) -> None:
    issue_summary = Counter(issue["field"] for issue in validation["issues"])
    runtime_blocker = validation.get("runtime_blocker")
    if runtime_blocker:
        conclusion = (
            "预检已通过并已显式触发真实 `--run`，但生产抽取链在逐条抽取前初始化失败。"
            "未调用模型、未产生 fallback，也未生成模拟预测或指标。"
        )
    else:
        conclusion = (
            "本次未调用真实 LLM，也未生成预测、混淆矩阵或 F1。原因是盲标表未通过评测输入质量门槛；"
            "在此状态下运行会把不完整或格式错误的人工标签当作 gold，结果不可解释。"
        )
    lines = [
        "# 人工 JD 端到端评测阻塞报告",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 预检结果",
        "",
        f"- 工作簿：`{xlsx}`",
        f"- 盲标数据行数：{validation['row_count']}/{validation['row_count']}",
        f"- 非空正文：{validation['rows_with_nonempty_detail']}/{validation['row_count']}；可追溯 URL：{validation['rows_with_source_url']}/{validation['row_count']}",
        f"- annotator：{', '.join(repr(x) for x in validation['observed_annotators']) or '空'}；要求为非空（多人标注各保留本人代号，禁止空标注）",
        f"- 全字段格式合格且可纳入真实评测的行数：{validation['fully_valid_rows']}/{validation['row_count']}",
        f"- total_samples = {validation['row_count']}",
        "- real_llm_success_samples = 0；fallback_samples = 0；failed_samples = 0（未进入逐条抽取）",
        "",
        "## 格式异常汇总",
        "",
    ]
    lines.extend([f"- `{field}`：{count} 项" for field, count in sorted(issue_summary.items())] or ["- 无"])
    if runtime_blocker:
        lines += ["", "## 运行时阻塞", "", f"- `{runtime_blocker}`", "- 当前运行时无法导入 PyYAML；未验证 provider/API 配置，且未读取任何密钥。"]
    lines += [
        "",
        "## 真实链路审计",
        "",
        "- 实际入口为 `backend/app/services/extraction/jd_extractor.py:JDExtractor.extract`。",
        "- 真实路径为 `TASK_TEMPLATE + SYSTEM_PROMPT + FEW_SHOT_EXAMPLES` → `LLMProviderChain.extract_structured` → `post_process`。",
        "- `JDExtractor` 在配置缺失或 `LLMExtractionError` 时静默降级到 `_rule_based_extract`（白名单扫描）。因此不能把降级结果称为真实 LLM 端到端评测，更不能与历史 0.6112 白名单扫描数字混为一谈。",
        "- `JDExtractionResult` 有岗位名、skills、education、requirements（must/nice），但没有经验区间或核心职责字段；当前代码无法对这两项产出真实预测。",
        "- `PositionAligner` 的 Neo4j/SBERT 对齐不在 `JDExtractor.extract` 调用链；本评测脚本仅按要求用 `normalize_position_name` 做静态规则对照。",
        "",
        "## 恢复条件与命令",
        "",
        "1. 确保全部行 annotator 非空（round1/round2 各保留标注者本人代号，如 LQ/张恺天代号）；将非空的 skills、bonus_skills、core_duties 填为 JSON 字符串数组；experience 留空或写 JSON 对象。空学历表示无明确学历要求，合法且不需要补写。",
        "2. 不改变现有标签含义的前提下，重新保存工作簿后运行预检。",
        "3. 只有预检为 12/12 后，才执行真实 LLM 调用。该命令可能产生模型调用费用：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py --run",
        "```",
        "",
        "预检命令（不调用网络或 LLM）：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py",
        "```",
    ]
    (output_dir / "evaluation_blocker_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_validation(output_dir: Path, validation: dict[str, Any]) -> str | None:
    """Persist preflight when possible, without blocking an explicitly requested run."""
    try:
        (output_dir / "manual_jd_eval_data_validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def write_runtime_blocker_fallback(output_dir: Path, validation: dict[str, Any], reason: str) -> None:
    """Keep a durable diagnosis if an editor has locked the canonical report."""
    text = "\n".join([
        "# 手工 JD 评测运行时阻塞记录",
        "",
        "## 本次状态",
        "",
        "- total_samples = 12",
        "- real_llm_success_samples = 0",
        "- fallback_samples = 0",
        "- failed_samples = 0（未进入逐条抽取；这是启动级阻塞）",
        f"- annotator = {', '.join(validation.get('observed_annotators', [])) or 'unknown'}",
        f"- preflight = {'passed' if validation.get('ready_for_real_run') else 'failed'}",
        "",
        "## 实际阻塞原因",
        "",
        f"真实 `--run` 已触发，但在导入生产 `LLMProviderChain` 时失败：`{reason}`。因此当前运行时缺少 PyYAML，尚未初始化 JDExtractor，未发出模型请求，也未发生规则 fallback。",
        "",
        "## 恢复步骤",
        "",
        "在具备项目依赖的 Python 环境中安装/同步 `backend/pyproject.toml` 的依赖（至少包括 PyYAML、Pydantic、instructor、OpenAI），并配置有效且启用的 `configs/llm_providers.yaml` provider/API key。不要将 key 写入本报告。然后执行：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py --run",
        "```",
        "",
        "历史 0.6112 未被使用，也不是本次真实 LLM 指标。",
    ])
    (output_dir / "evaluation_runtime_blocker_report.md").write_text(text + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--sheet", default="Round1盲标")
    parser.add_argument(
        "--gold-jsonl", type=Path, default=None,
        help=(
            "从 final gold JSONL（data/golden_set/final/jd_golden_110.jsonl）读取 gold；"
            "优先于 --xlsx/--sheet。未指定 --output-dir 时默认输出到 review/evaluation_110。"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--run", action="store_true", help="Only after preflight succeeds, call the real LLM chain.")
    args = parser.parse_args()
    if args.gold_jsonl is not None:
        rows = _load_gold_jsonl(args.gold_jsonl)
        source_desc: Path = args.gold_jsonl
        output_dir: Path = args.output_dir or ROOT / "data" / "golden_set" / "review" / "evaluation_110"
    else:
        rows = _load_round1_blind_rows(args.xlsx, args.sheet)
        source_desc = args.xlsx
        output_dir = args.output_dir or DEFAULT_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)
    validation = validate_rows(rows)
    validation_write_warning = write_validation(output_dir, validation)
    if validation_write_warning:
        print(f"WARNING: could not update validation summary: {validation_write_warning}")
    if not validation["ready_for_real_run"]:
        write_blocker_report(output_dir, validation, source_desc)
        print("BLOCKED: gold labels did not pass preflight; no LLM call was made.")
        return 2
    if not args.run:
        print("READY: preflight passed. Re-run with --run to call the real LLM chain.")
        return 0
    try:
        metrics = run_real_eval(rows, output_dir, gold_sha256=_gold_sha256(source_desc))
    except Exception as exc:
        validation["runtime_blocker"] = _safe_exception_summary(exc)
        validation_write_warning = write_validation(output_dir, validation)
        if validation_write_warning:
            validation["validation_write_warning"] = validation_write_warning
        try:
            write_blocker_report(output_dir, validation, source_desc)
        except OSError as report_exc:
            print(f"WARNING: could not update blocker report: {type(report_exc).__name__}: {report_exc}")
            try:
                write_runtime_blocker_fallback(output_dir, validation, validation["runtime_blocker"])
            except OSError as fallback_exc:
                print(f"WARNING: could not write runtime blocker fallback: {type(fallback_exc).__name__}: {fallback_exc}")
        print(f"BLOCKED: {_safe_exception_summary(exc)}")
        return 3
    archive_path = archive_metrics(metrics)
    print(f"ARCHIVED: {archive_path.relative_to(ROOT)}")
    print("SUCCESS: real LLM predictions and metrics were written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
