"""技能治理列表纯函数测试（build_skill_admin_page）。

端点 /admin/skills 的数据组装逻辑为纯函数，便于不触库直测。覆盖：
白名单 ∪ approved 别名聚合、去重、in_whitelist 标记、q/白名单过滤、分页。
测试数据驱动（动态取白名单成员 / 注入唯一别名标准名），不臆测 yaml 内容。
"""

from app.api.v1.admin_routes.skills import build_skill_admin_page
from app.services.extraction.dictionary import SKILL_WHITELIST


def _first_whitelist_name() -> str:
    return next(iter(SKILL_WHITELIST))


def test_whitelist_union_aliases_raw():
    """白名单 ∪ approved 别名聚合：注入唯一标准名的别名去重聚合 + 白名单态标记。"""
    std = "UnicornSpecialQA"
    approved = [("UniA", std), ("UniA", std), ("UniB", std)]
    items, total = build_skill_admin_page(
        approved_aliases=approved, q=std, category="", whitelist="all", noise="all", page=1, size=50,
    )
    assert total == 1
    by_name = {it["name"]: it for it in items}
    assert std in by_name
    assert set(by_name[std]["aliases"]) == {"UniA", "UniB"}  # 同 standard 去重
    assert by_name[std]["in_whitelist"] == (std in SKILL_WHITELIST)


def test_whitelist_union_includes_whitelist_member():
    """白名单成员必含（in_whitelist=True），whitelist=only 仅返回白名单。"""
    wl = _first_whitelist_name()
    items, total = build_skill_admin_page(
        approved_aliases=[], q="", category="", whitelist="only", noise="all", page=1, size=10_000,
    )
    names = {it["name"]: it for it in items}
    assert wl in names
    assert all(it["in_whitelist"] for it in items)
    assert total == len(items)


def test_filter_whitelist_exclude_contains_injected_standard():
    """whitelist=exclude：注入的非白名单标准名应出现在结果且 in_whitelist=False。"""
    std = "VariantOnlyZZ"
    items, _ = build_skill_admin_page(
        [("", std)], q=std, category="", whitelist="exclude", noise="all", page=1, size=50,
    )
    assert [it["name"] for it in items] == [std]
    assert items[0]["in_whitelist"] is False


def test_filter_q_matches_alias():
    """q 命中别名的标准名（非白名单标准名）被检索出来。"""
    std = "JavaScriptNonStd"
    items, total = build_skill_admin_page(
        [("QzxUniqueAlias", std)], q="QzxUniqueAlias", category="", whitelist="all",
        noise="all", page=1, size=50,
    )
    assert total == 1
    assert items[0]["name"] == std


def test_pagination_slices():
    """分页切片：page=2/size=2 与 page=1 的 total 一致，仅返回当前页。"""
    _, total = build_skill_admin_page([], q="", category="", whitelist="all", noise="all",
                                      page=1, size=2)
    paged, paged_total = build_skill_admin_page(
        [], q="", category="", whitelist="all", noise="all", page=2, size=2,
    )
    assert total == paged_total
    assert len(paged) == 2


def test_items_have_complete_keys():
    """列表项字段结构完整（aliases 数组存在）。"""
    wl = _first_whitelist_name()
    items, _ = build_skill_admin_page(
        [("Jsx", "JSXNonStd"), ("", wl)], q="", category="", whitelist="all", noise="all",
        page=1, size=10_000,
    )
    for it in items:
        assert set(it.keys()) == {"name", "category", "in_whitelist", "is_noise", "aliases"}
        assert isinstance(it["aliases"], list)