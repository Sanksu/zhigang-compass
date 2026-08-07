"""zhilian 详情页解析测试（extract_job_detail）。

覆盖：正常解析（SSR __INITIAL_STATE__ 内嵌正文 → 岗位职责/任职要求拆分）、
无 SSR、正文缺失、无「任职要求」小节时整段归职责。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.zhilian_detail import extract_job_detail, _html_to_text, _split_sections


def _detail_html(job_desc_html: str = "") -> str:
    """构造含 __INITIAL_STATE__ 的详情页 HTML（最小可解析）。"""
    state = {
        "jobNumber": "CC0001",
        "jobDetail": {"detailedPosition": {"description": job_desc_html}},
    }
    return (
        f"<html><script>__INITIAL_STATE__="
        f"{json.dumps(state, ensure_ascii=False)}</script></html>"
    )


def test_extract_normal_split():
    """完整正文正确拆分为岗位职责/任职要求两段。"""
    html = _detail_html(
        "<div> 岗位职责:</div><div> 1. 负责核心模块研发；</div>"
        "<div> 任职要求:</div><div> 1. 本科以上学历；</div>"
    )
    result = extract_job_detail(html)
    assert result["description"] == "岗位职责:\n1. 负责核心模块研发；"
    assert result["requirements"] == "任职要求:\n1. 本科以上学历；"


def test_extract_no_initial_state():
    """页面无 SSR 数据（验证码页/加载失败）返回空串，不抛异常。"""
    assert extract_job_detail("<html>Security Verification</html>") == {
        "description": "",
        "requirements": "",
    }


def test_extract_missing_description():
    """SSR 存在但正文字段缺失返回空串。"""
    html = (
        "<html><script>__INITIAL_STATE__="
        '{"jobDetail":{"detailedPosition":{}}}'
        "</script></html>"
    )
    assert extract_job_detail(html) == {"description": "", "requirements": ""}


def test_split_no_requirement_section():
    """无「任职要求」小节时整段归岗位职责，不丢正文。"""
    assert _split_sections("岗位职责:\n1. 开发\n2. 测试") == {
        "description": "岗位职责:\n1. 开发\n2. 测试",
        "requirements": "",
    }


def test_extract_falls_back_to_job_desc():
    """description 缺失时回退 jobDesc（同正文的别名键）。"""
    state = {
        "jobDetail": {
            "detailedPosition": {
                "jobDesc": "<div> 工作职责:</div><div> 开发</div>"
                "<div> 任职要求:</div><div> 本科</div>"
            }
        }
    }
    html = (
        f"<html><script>__INITIAL_STATE__="
        f"{json.dumps(state, ensure_ascii=False)}</script></html>"
    )
    result = extract_job_detail(html)
    assert result["description"] == "工作职责:\n开发"
    assert result["requirements"] == "任职要求:\n本科"


def test_extract_invalid_initial_state_json():
    """__INITIAL_STATE__ 非合法 JSON 时返回空串，不抛异常。"""
    html = "<html><script>__INITIAL_STATE__={not json}</script></html>"
    assert extract_job_detail(html) == {"description": "", "requirements": ""}


def test_split_other_requirement_titles():
    """「岗位要求/职位要求」标题同样触发职责/要求拆分。"""
    assert _split_sections("岗位职责:\n开发\n岗位要求:\n本科") == {
        "description": "岗位职责:\n开发",
        "requirements": "岗位要求:\n本科",
    }
    assert _split_sections("工作内容:\n开发\n职位要求:\n硕士") == {
        "description": "工作内容:\n开发",
        "requirements": "职位要求:\n硕士",
    }


def test_html_to_text_block_tags_become_lines():
    """块级标签（div/li/br）转行、行内标签（span）保留文本、空白折叠。"""
    html = (
        "<div> 岗位职责:</div><li>1. 负责开发</li><li>2. 负责测试</li>"
        "<span>忽略</span><br>第三行"
    )
    assert _html_to_text(html) == "岗位职责:\n1. 负责开发\n2. 负责测试\n忽略\n第三行"
