"""PII 脱敏单元测试（设计文档 §8.2，覆盖率要求 ≥ 90%）。

覆盖：手机/身份证/邮箱/中文姓名的掩码替换、映射返回、
技术名词不被误伤、身份证先于手机号处理避免误脱敏。
"""

from app.services.resume.pii_mask import mask_pii


class TestMaskPii:
    def test_phone_masked(self):
        masked, _ = mask_pii("联系电话：13800138000")
        assert masked == "联系电话：[PHONE]"

    def test_id_card_masked(self):
        masked, _ = mask_pii("身份证号 110105199003071234")
        assert masked == "身份证号 [ID_CARD]"

    def test_email_masked(self):
        masked, _ = mask_pii("邮箱 zhangsan@example.com")
        assert masked == "邮箱 [EMAIL]"

    def test_name_masked_after_colon(self):
        masked, _ = mask_pii("姓名：张三")
        assert masked == "姓名：[NAME]"

    def test_two_char_given_name_masked(self):
        masked, _ = mask_pii("王小明 男 26岁")
        assert masked == "[NAME] 男 26岁"

    def test_id_phone_email_together(self):
        # 身份证前 6 位（110105）不以 1[3-9] 开头，但 13/14/15 开头的行政区划
        # 需验证身份证先替换后手机号不会从身份证数字中误匹配
        text = "姓名：李四\n身份证 130105199003071234\n手机 13800138000\n邮箱 a@b.com"
        masked, _ = mask_pii(text)
        assert "[ID_CARD]" in masked
        assert "[PHONE]" in masked
        assert "[EMAIL]" in masked
        # 身份证被整段替换后，不应残留 11 位数字碎片
        assert "1301051990" not in masked

    def test_technical_term_not_masked(self):
        masked, _ = mask_pii("技能：Python、Java、人工智能")
        assert "Python" in masked
        assert "Java" in masked
        assert "人工智能" in masked
        assert "[NAME]" not in masked

    def test_name_inside_word_not_masked(self):
        # 姓氏后紧跟汉字且非姓名边界（如"王国系统"）不误伤
        masked, _ = mask_pii("参与王国系统架构设计")
        assert "王国系统" in masked
        assert "[NAME]" not in masked

    def test_mapping_returns_first_original(self):
        _, mapping = mask_pii("电话 13800138000 邮箱 a@b.com")
        assert mapping["[PHONE]"] == "13800138000"
        assert mapping["[EMAIL]"] == "a@b.com"

    def test_no_pii_unchanged(self):
        masked, mapping = mask_pii("无敏感信息的纯技术文本")
        assert masked == "无敏感信息的纯技术文本"
        assert mapping == {}
