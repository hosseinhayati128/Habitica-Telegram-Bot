from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_sends_only_signed_init_data_as_authorization():
    source = (ROOT / "static" / "miniapp" / "app.js").read_text()

    assert "telegram?.initData" in source
    assert "Authorization: `tma ${rawInitData}`" in source
    assert "initDataUnsafe" not in source
    assert "telegram_user_id" not in source
    assert "habitica_user_id" not in source
    assert "API_KEY" not in source


def test_frontend_loads_profile_and_avatar_independently_as_a_blob():
    source = (ROOT / "static" / "miniapp" / "app.js").read_text()

    assert source.index("const profileOk = await loadProfile()") < source.index(
        "const avatarOk = await loadAvatar(forceAvatar)"
    )
    assert "response.blob()" in source
    assert "URL.createObjectURL" in source
    assert "URL.revokeObjectURL" in source
    assert '"?refresh=1"' in source


def test_theme_bootstrap_runs_before_stylesheet_and_supports_three_modes():
    template = (ROOT / "templates" / "miniapp" / "index.html").read_text()
    bootstrap = (ROOT / "static" / "miniapp" / "theme-init.js").read_text()
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()

    assert template.index("theme-init.js") < template.index("app.css")
    assert 'new Set(["auto", "light", "dark"])' in bootstrap
    assert 'const THEME_KEY = "hh_theme_mode"' in application
    assert "CloudStorage" in application
    assert 'telegram?.onEvent?.("themeChanged"' in application
    assert "prefers-color-scheme: dark" in application


def test_template_has_accessible_tabs_and_private_data_skeleton_only():
    template = (ROOT / "templates" / "miniapp" / "index.html").read_text()

    assert 'role="tablist"' in template
    assert template.count('role="tab"') == 4
    assert template.count('role="tabpanel"') == 4
    assert 'aria-live="polite"' in template
    assert "USER_ID" not in template
    assert "API_KEY" not in template
    assert "botdata" not in template
