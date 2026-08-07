"""智联招聘详情页正文解析（__INITIAL_STATE__ SSR JSON → 岗位职责/任职要求）。

详情页正文不在列表页（列表页仅有 title/薪资/标签等摘要），需请求
`https://www.zhaopin.com/jobdetail/{number}.htm` 后解析 SSR 数据：
- `__INITIAL_STATE__` 内嵌 JSON，正文位于 `jobDetail.detailedPosition.description`
  （HTML，含「岗位职责: … 任职要求: …」小节）
- 解析结果供 spiders/zhilian.py（新采集）与 scripts/backfill_jd_detail.py
  （存量回填）复用，避免两处维护重复解析逻辑
"""

import json
import re

# 非贪婪到 </script> 为止（__INITIAL_STATE__ 是独立 script 标签，非页面尾部）
_INITIAL_STATE_RE = re.compile(
    r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL
)
# 正文小节标题：按「任职要求」类标题把正文拆成职责/要求两段
_REQ_TITLE_RE = re.compile(r"任职要求|岗位要求|职位要求")


def extract_job_detail(html: str) -> dict:
    """从 zhilian 详情页 HTML 提取岗位职责/任职要求纯文本。

    Returns:
        {"description": str, "requirements": str}；解析失败（无 SSR/字段缺失）
        时两字段均为空串，由调用方按缺失处理（不中断采集）。
    """
    match = _INITIAL_STATE_RE.search(html)
    if not match:
        return {"description": "", "requirements": ""}
    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return {"description": "", "requirements": ""}
    dp = (data.get("jobDetail") or {}).get("detailedPosition") or {}
    raw = dp.get("description") or dp.get("jobDesc") or ""
    if not raw:
        return {"description": "", "requirements": ""}
    return _split_sections(_html_to_text(raw))


def _html_to_text(html: str) -> str:
    """块级标签（div/li/p/br）→ 换行，其余标签删除，逐行折叠空白。"""
    text = re.sub(r"</?(?:div|li|p|br|ul|ol|tr)[^>]*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _split_sections(text: str) -> dict:
    """按「任职要求」小节标题拆分：职责在前、要求在后（含小节标题保留原样）。

    少数 JD 无要求小节时整段归职责，避免丢正文。
    """
    m = _REQ_TITLE_RE.search(text)
    if m:
        return {
            "description": text[: m.start()].strip(),
            "requirements": text[m.start():].strip(),
        }
    return {"description": text.strip(), "requirements": ""}
