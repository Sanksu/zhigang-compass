"""简历 PII 脱敏（设计文档 §8.2，PIPL/GDPR 合规前置步骤）。

简历文本在送入 LLM 抽取前必须先经 `mask_pii` 脱敏，避免敏感信息泄露给外部 LLM API。
脱敏映射（{placeholder: original}）仅存于内存，不落日志、不入数据库。

脱敏顺序说明：身份证号先于手机号处理——身份证前 6 位行政区划码（如 13/14/15 开头）
可能被手机号正则误命中，先替换成占位符即避免二次误脱敏。
"""

import re

# ── 脱敏正则 ──
_ID_CARD_RE = re.compile(
    r"[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
)
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")

# 常见姓氏（百家姓常见集），用于中文姓名启发式匹配
_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和"
    "穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮"
    "蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支"
    "柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑"
    "裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车"
    "侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎"
    "蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳"
    "逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充"
    "慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧"
    "殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关"
    "蒯相查后荆红游竺权逯盖益桓公"
)
# 姓名启发式：姓氏 + 1~2 个汉字，前后均不得紧邻汉字（避免误伤技术名词/词语内部）
_NAME_RE = re.compile(
    r"(?<![\u4e00-\u9fff])([{}][\u4e00-\u9fff]{{1,2}})(?![\u4e00-\u9fff])".format(_SURNAMES)
)

# 按脱敏顺序排列：(正则, 占位符)
_PII_PATTERNS = (
    (_ID_CARD_RE, "[ID_CARD]"),
    (_PHONE_RE, "[PHONE]"),
    (_EMAIL_RE, "[EMAIL]"),
    (_NAME_RE, "[NAME]"),
)


def mask_pii(text: str) -> tuple[str, dict[str, str]]:
    """脱敏简历文本中的 PII（身份证/手机/邮箱/中文姓名 → 占位符）。

    Returns:
        (脱敏文本, 映射 {placeholder: 原始值})。
        同一类型多处命中时生成带序号的唯一占位符（如 [PHONE_2]），首个命中保留
        无序号占位符（[PHONE]），保证 restore_pii 时各占位符回填各自的原值，
        而不是全部回填为首个命中值。映射仅供内存使用（如 UI 回显原文），
        不落日志、不入数据库。
    """
    masked = text
    mapping: dict[str, str] = {}
    for pattern, placeholder in _PII_PATTERNS:
        hits: list[str] = []

        def _replace(m: re.Match, placeholder: str = placeholder, hits: list[str] = hits) -> str:
            hits.append(m.group(0))
            if len(hits) == 1:
                return placeholder
            return f"{placeholder[:-1]}_{len(hits)}]"

        masked = pattern.sub(_replace, masked)
        if not hits:
            continue
        mapping[placeholder] = hits[0]
        for i, original in enumerate(hits[1:], start=2):
            mapping[f"{placeholder[:-1]}_{i}]"] = original
    return masked, mapping


def restore_pii(parsed: dict, mapping: dict[str, str]) -> dict:
    """将结构化抽取结果中的脱敏占位符回填为原始值（设计文档 §8.2）。

    LLM 抽取在脱敏文本上进行，name/phone/email/教育经历等字段可能携带
    [NAME]/[PHONE]/[EMAIL]/[ID_CARD] 占位符；回填映射仅在当前任务内存中
    存活，不回填日志。映射不含某占位符时保留原占位符（该类型原文未被
    脱敏命中，属正常状态）。同一类型多处命中时占位符带序号
    （如 [PHONE_2]），映射按 key 一一回填各自原值。
    """
    if not mapping:
        return parsed

    def _restore(value):
        if isinstance(value, str):
            for placeholder, original in mapping.items():
                if placeholder in value:
                    value = value.replace(placeholder, original)
            return value
        if isinstance(value, list):
            return [_restore(v) for v in value]
        if isinstance(value, dict):
            return {k: _restore(v) for k, v in value.items()}
        return value

    return _restore(parsed)
