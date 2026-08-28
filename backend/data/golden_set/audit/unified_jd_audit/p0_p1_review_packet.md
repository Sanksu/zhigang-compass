# P0/P1 最终证据复核与修复决策包
- 生成时间（脚本执行时）：运行时审计派生
- 审计目标：ANN-0023 P0 + 2条P1 Zhilian切分失败 + 2条SHA legacy
- Gold110 vs Zhilian exact overlap: 110 = EXPECTED_PARENT_CHILD_OVERLAP

---

## §一 P0：Gold ANN-0023专项复核
### 1.1 Gold正式字段清单（只读，不创造字段）
- 存在`review_gold_requirements`字段？：**NO**（注意：项目实际Gold字段前缀=`gold_`，没有`review_gold_requirements`字段）
- 实际Gold标注字段（用于判断A-D）：`gold_skills, gold_bonus_skills, gold_experience, gold_education, gold_core_duties, gold_title`
- Gold全部gold_*字段列表：`gold_bonus_skills, gold_core_duties, gold_education, gold_experience, gold_skills, gold_title`

### 1.2 基础信息（样本对齐）
- sample_id：**ANN-0023**
- source_id：CC148739350J40212149403（Gold侧 / Zhilian同父）
- job_title_raw：'单片机工程师' / '单片机工程师'
- company_name：'北京航峰科伟装备技术股份有限公司' / '北京航峰科伟装备技术股份有限公司'
- source_education（原始爬取）：'本科' / '本科'
- source_experience（原始爬取）：'3-5年' / '3-5年'
- text_education（后处理）：'本科以上学历'
- text_experience（后处理）：'3年以上'

### 1.3 字段内容大小（无长正文粘贴）
- Gold responsibilities 长度=226，requirements 长度=0，detail_raw_text长度=226
- Zhilian responsibilities 长度=113，requirements 长度=112，detail_raw_text长度=226
- Gold==Zhilian resp? 内容匹配=0.67；req匹配=0.00
- Zhilian resp内容类型：要求关键词命中 4 个（['熟悉', '熟练', '掌握', '具备']）；职责动词命中 5 个（['design', '开发', '设计']）；resp_req_swapped_suspect=False

### 1.4 A-D问答
**A. Gold的review_gold_requirements是否存在该字段？**
- 答：**否**。Gold正式字段无`review_gold_requirements`；本项目Gold实际使用：`gold_skills / gold_bonus_skills / gold_experience / gold_education / gold_core_duties / gold_title` 六字段。

**B. ANN-0023当前被判P0的真正原因是什么？**
- 4. Gold gold_core_duties="['硬件产品开发', '固件设计', 'FPGA及CPLD开发应用', '原理图及PCB设计', '硬件设计、开发、测试、维护']"，要求词=0，职责动词=4 → 需人工判断是否混淆

**C. 根据原始JD正文：是否存在明确的任职要求/资格要求内容？**
- 答：**YES**
- 证据摘要：heading_hits=[]; total_lines_in_detail=6; req_keyword_hit_lines=6 (100%); detail_length=226
- resp全文（=detail全文）逐行清单：
  - 行1：4、熟悉FPGA及CPLD的开发应用，熟悉Quartus开发工具；
  - 行2：5、熟练掌握Protel99se及Altium Designer等开发工具，具备独立设计原理图和PCB的能力；
  - 行3：6、熟悉硬件设计、开发、测试、维护的各个环节。

**D. Gold当前人工结果是否违反现有标注规则？**
- 答：**GOLD_CORRECT**
- 判定依据：
  - gold_core_duties 内容（长度65）："['硬件产品开发', '固件设计', 'FPGA及CPLD开发应用', '原理图及PCB设计', '硬件设计、开发、测试、维护']"
    - 其中要求词命中=0，职责动词命中=4
  - gold_skills 非空? YES 143字符; 预览："['C', '汇编', 'Verilog', '51单片机', 'STM32', 'FPGA', 'CPLD', 'Quartus', 'Protel 99SE', 'Altium Designer', '模拟电路', '数字电路', '原理图设计', 'PCB设计', '硬件设计']"
  - gold_education 非空? YES 2; 值：'本科'
  - gold_experience 非空? YES 35; 值："{'min_years': 3, 'max_years': None}"
  - gold_bonus_skills 非空? YES 2; 预览：'[]'
  - gold_title 非空? YES 6; 值：'单片机工程师'

**⚠ 结论：**
- ANN-0023 gold_core_duties内容为职责动词，gold_skills/education/experience与原resp中要求词正确映射=GOLD_CORRECT；Baseline门槛降至 **BASELINE_CAN_PROCEED_WITH_CANDIDATE_REPAIR_PENDING**。

---

## §二 P1：两个Zhilian requirements切分失败专项复核

### P1：CC148739350J40212149403 - 单片机工程师
- company: 北京航峰科伟装备技术股份有限公司
- responsibilities长度=113；requirements长度=112；detail_raw_text长度=226
- detail中是否存在明确要求类结构性段落？
  - 标题命中：无明确要求标题，要求关键词占比低=100%
  - 判定：**SOURCE_TRUE_MISSING**
  - 建议responsibilities边界：保留responsibilities[0:113]（确认为职责）
  - 建议requirements边界：无（原JD未提供独立任职要求段）

### P1：CC404298980J40856902010 - 嵌入式开发工程师（央企/内网开发）
- company: 北京百米互联科技有限公司
- responsibilities长度=41；requirements长度=117；detail_raw_text长度=159
- detail中是否存在明确要求类结构性段落？
  - 标题命中：无明确要求标题，要求关键词占比低=60%
  - 判定：**SOURCE_TRUE_MISSING**
  - 建议responsibilities边界：保留responsibilities[0:41]（确认为职责）
  - 建议requirements边界：无（原JD未提供独立任职要求段）


---

## §三 检查P1是否影响Gold（exclusion manifest匹配）

- CC148739350J40212149403 (单片机工程师)
  - in_gold_manifest(=Gold exact overlap 对应父candidate？)：**true**
  - 对应Gold sample_id：**ANN-0023**
  - ⚠ 升级为P0候选（与ANN-0023同一记录）：Gold证据源头切分错误+继承req空

- CC404298980J40856902010 (嵌入式开发工程师（央企/内网开发）)
  - in_gold_manifest(=Gold exact overlap 对应父candidate？)：**false**
  - 不进入Gold110 → 不会直接影响当前Gold110评测（仅为candidate侧修bug）


---

## §四 两个SHA异常是否阻断

- CC148739350J40212149403 - 单片机工程师
  - orig_sha前16位：`d5db8037f48c62d2`；公式SHA256(resp+\\n+req)前16位：`d5db8037f48c62d2`
  - legacy规则命中：`current_resp_nl_req`
  - 是否追溯（hash可溯源到明确规则）：True
  - 是否属于Gold lineage（即Gold110的父candidate？）：True
  - detail_raw_text == responsibilities? False
  - 最终分类：**MATCH（不应出现在异常列表）**

- CC404298980J40856902010 - 嵌入式开发工程师（央企/内网开发）
  - orig_sha前16位：`27d2c833d9155b7f`；公式SHA256(resp+\\n+req)前16位：`27d2c833d9155b7f`
  - legacy规则命中：`current_resp_nl_req`
  - 是否追溯（hash可溯源到明确规则）：True
  - 是否属于Gold lineage（即Gold110的父candidate？）：False
  - detail_raw_text == responsibilities? False
  - 最终分类：**MATCH（不应出现在异常列表）**

- 整体判定：2条均为NON_BLOCKING_LEGACY_HASH？**False**


---

## §五 决策文件清单

- `p0_p1_review_packet.md`（本文件）
- `p0_p1_review_decisions.csv`（5行决策行：1×P0 + 2×P1 + 2×SHA）

## §六 Baseline门槛判断（严格三态）

判定结果：**BASELINE_CAN_PROCEED_WITH_CANDIDATE_REPAIR_PENDING**

- 理由：Gold ANN-0023 gold_core_duties已独立人工标注正确，Gold标注无误，不阻塞进入Baseline；剩余2条P1为Zhilian candidate切分问题，其中1条是Gold父记录但不直接影响Gold使用（Gold标注正确）；可同时启动Baseline并后台并行修复切分+hash legacy说明。
