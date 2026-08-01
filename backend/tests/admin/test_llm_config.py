"""LLM provider 配置持久化的纯函数测试。"""

import yaml

from app.api.v1.admin import mask_secret, validate_providers, save_llm_config, load_llm_config


def _valid_providers():
    return [
        {"name": "deepseek", "priority": 1, "base_url": "https://api.deepseek.com/v1",
         "api_key": "sk-test-1234", "model": "deepseek-v4-flash", "enabled": True},
        {"name": "spark", "priority": 2, "base_url": "https://spark-api.xf-yun.com/v1",
         "api_key": "", "model": "v4.0", "enabled": True},
    ]


# ---- mask_secret ----

def test_mask_secret_empty():
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_mask_secret_short():
    assert mask_secret("abc") == "***"


def test_mask_secret_normal():
    assert mask_secret("sk-test-1234") == "********1234"
    assert mask_secret("sk-test-1234").endswith("1234")


# ---- validate_providers ----

def test_validate_providers_ok():
    assert validate_providers(_valid_providers()) is None


def test_validate_providers_empty():
    assert validate_providers([]) is not None
    assert validate_providers(None) is not None


def test_validate_providers_missing_name():
    assert validate_providers([{"priority": 1, "base_url": "https://a.com",
                                "model": "m", "enabled": True}]) is not None


def test_validate_providers_invalid_name_char():
    assert validate_providers([{"name": "bad name!", "priority": 1,
                                "base_url": "https://a.com", "model": "m", "enabled": True}]) is not None


def test_validate_providers_duplicate_name():
    p1, p2 = _valid_providers()
    p2["name"] = "deepseek"
    assert validate_providers([p1, p2]) is not None


def test_validate_providers_bad_base_url():
    p = _valid_providers()[0]
    p["base_url"] = "api.deepseek.com/v1"
    assert validate_providers([p]) is not None


def test_validate_providers_missing_model():
    p = _valid_providers()[0]
    p["model"] = ""
    assert validate_providers([p]) is not None


def test_validate_providers_bad_priority():
    p = _valid_providers()[0]
    p["priority"] = 0
    assert validate_providers([p]) is not None


def test_validate_providers_duplicate_priority():
    p1, p2 = _valid_providers()
    p2["priority"] = 1
    assert validate_providers([p1, p2]) is not None


def test_validate_providers_bad_enabled():
    p = _valid_providers()[0]
    p["enabled"] = "yes"
    assert validate_providers([p]) is not None


# ---- save_llm_config / load_llm_config ----

def test_save_llm_config_preserves_header_comment(tmp_path):
    path = tmp_path / "llm_providers.yaml"
    path.write_text(
        "# ============ 头部注释 ============\n"
        "# 单一事实源\n"
        "providers:\n"
        "  - name: deepseek\n"
        "    priority: 1\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "    api_key: sk-old-secret\n"
        "    model: m1\n"
        "    enabled: true\n"
        "routing:\n"
        "  sync:\n"
        "    timeout_seconds: 10\n",
        encoding="utf-8",
    )
    save_llm_config(path, _valid_providers())
    text = path.read_text(encoding="utf-8")
    assert "# ============ 头部注释 ============" in text
    assert "# 单一事实源" in text
    data = yaml.safe_load(text)
    assert data["routing"]["sync"]["timeout_seconds"] == 10  # 非 providers 段保留
    assert [p["name"] for p in data["providers"]] == ["deepseek", "spark"]


def test_save_llm_config_masked_key_keeps_old(tmp_path):
    path = tmp_path / "llm_providers.yaml"
    path.write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    priority: 1\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "    api_key: sk-old-secret\n"
        "    model: m1\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    providers = _valid_providers()
    providers[0]["api_key"] = "****1234"  # 掩码 → 保持原值
    save_llm_config(path, providers)
    saved = load_llm_config(path)
    assert saved["providers"][0]["api_key"] == "sk-old-secret"


def test_save_llm_config_empty_key_keeps_old(tmp_path):
    path = tmp_path / "llm_providers.yaml"
    path.write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    priority: 1\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "    api_key: sk-old-secret\n"
        "    model: m1\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    providers = _valid_providers()
    providers[0]["api_key"] = ""  # 留空 → 保持原值
    save_llm_config(path, providers)
    saved = load_llm_config(path)
    assert saved["providers"][0]["api_key"] == "sk-old-secret"


def test_save_llm_config_new_plain_key_updates(tmp_path):
    path = tmp_path / "llm_providers.yaml"
    path.write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    priority: 1\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "    api_key: sk-old-secret\n"
        "    model: m1\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    providers = _valid_providers()
    providers[0]["api_key"] = "sk-new-plain"
    save_llm_config(path, providers)
    saved = load_llm_config(path)
    assert saved["providers"][0]["api_key"] == "sk-new-plain"


def test_save_llm_config_invalid_raises(tmp_path):
    path = tmp_path / "llm_providers.yaml"
    path.write_text("providers: []\n", encoding="utf-8")
    try:
        save_llm_config(path, [{"name": "x", "priority": 1, "base_url": "bad-url",
                                "model": "", "enabled": True}])
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
