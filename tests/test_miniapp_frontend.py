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


def test_all_task_tabs_replace_placeholders_with_independent_state_hooks():
    template = (ROOT / "templates" / "miniapp" / "index.html").read_text()

    for task_type in ("habit", "daily", "todo"):
        assert f'data-task-page="{task_type}"' in template
    assert template.count("data-task-list") == 3
    assert template.count("data-task-loading") == 3
    assert template.count("data-task-empty") == 3
    assert template.count("data-task-error role=") == 3
    assert 'id="task-editor-dialog"' in template
    assert 'id="delete-dialog"' in template
    assert 'id="discard-dialog"' in template
    assert 'id="quick-add-dialog"' in template
    assert 'id="task-fab"' in template
    assert 'data-quick-add="true"' in template
    assert 'id="mini-profile"' in template
    assert 'id="editor-delete"' in template
    assert "Coming next</p><h1>Habits" not in template


def test_task_frontend_uses_safe_dom_and_authenticated_mutation_methods():
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()
    tasks = (ROOT / "static" / "miniapp" / "tasks.js").read_text()

    assert "HabiticaTaskUI.createTaskController" in application
    assert "requestJson" in application
    assert 'method: "PATCH"' in tasks
    assert 'method: "DELETE"' in tasks
    assert 'method: "POST"' in tasks
    assert "textContent" in tasks
    assert "innerHTML" not in tasks
    assert "API_KEY" not in tasks
    assert "USER_ID" not in tasks
    assert "initDataUnsafe" not in tasks


def test_task_css_keeps_mobile_content_clear_and_long_text_wrapped():
    styles = (ROOT / "static" / "miniapp" / "app.css").read_text()

    assert ".task-row__title" in styles
    assert ".task-row__edge" in styles
    assert ".task-mini-profile" in styles
    assert ".task-fab" in styles
    assert "overflow-wrap: anywhere" in styles
    assert ".task-dialog" in styles
    assert ".bottom-nav" in styles
    assert "--content-safe-bottom" in styles
    assert "@media (min-width: 760px)" in styles


def test_profile_state_is_shared_without_refreshing_avatar_after_scores():
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()
    tasks = (ROOT / "static" / "miniapp" / "tasks.js").read_text()

    assert "state.profilePayload" in application
    assert "renderMiniProfile" in application
    assert "scheduleProfileRefresh" in application
    assert "onMutationConfirmed: scheduleProfileRefresh" in application
    assert "profileCoordinator?.scheduleMutationRefresh" in application
    assert "onMutationConfirmed(detail)" in tasks
    assert "profilePatch" in tasks
    score_callback = application[application.index("function scheduleProfileRefresh") :]
    score_callback = score_callback[: score_callback.index("async function ensureTaskProfileLoaded")]
    assert "loadAvatar" not in score_callback


def test_collapsed_tasks_use_separate_content_and_scoring_actions():
    tasks = (ROOT / "static" / "miniapp" / "tasks.js").read_text()

    assert 'dataset.taskAction = "open"' in tasks
    assert "task-row__edge" in tasks
    assert '"task-row__completion"' in tasks
    assert 'action.dataset.taskAction === "score"' in tasks
    assert 'action.dataset.taskAction === "completion"' in tasks
    assert 'action.dataset.taskAction === "open"' in tasks
    assert '"task-card__actions"' not in tasks


def test_gameplay_startup_gate_blocks_tasks_until_authoritative_day_status():
    template = (ROOT / "templates" / "miniapp" / "index.html").read_text()
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()
    gameplay = (ROOT / "static" / "miniapp" / "gameplay.js").read_text()

    assert 'class="is-day-gated"' in template
    assert 'id="app-shell" aria-hidden="true" inert' in template
    assert 'id="day-gate"' in template
    assert 'id="day-review-submit"' in template
    assert '`${API_BASE}/day-status`' in application
    assert '`${API_BASE}/day-refresh`' in application
    assert application.index("void checkDayStatus()") > application.index("installTabs()")
    for state in (
        "checking_day",
        "ready",
        "review_required",
        "submitting_review",
        "refreshing_day",
        "refresh_failed",
    ):
        assert f'"{state}"' in gameplay
    assert "completedDailyIds" in application
    assert "resolvedDailyIds" in application
    assert "unresolvedDailyIds" in application
    assert 'batch_incomplete: "Recorded this batch.' in application
    assert "dayOutcomeUnknown" in application
    assert '"Check Day Status"' in application
    assert "continuePendingDayGate" in application


def test_health_potion_is_a_persistent_confirmed_post_action_without_avatar_refresh():
    template = (ROOT / "templates" / "miniapp" / "index.html").read_text()
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()
    styles = (ROOT / "static" / "miniapp" / "app.css").read_text()

    assert 'id="potion-fab"' in template
    assert 'aria-label="Buy and use Health Potion"' in template
    assert 'id="potion-dialog"' in template
    assert 'id="potion-submit" type="submit"' in template
    assert '`${API_BASE}/health-potion`' in application
    assert "potionPurchaseIntent" in application
    assert "payload.purchaseIntent" in application
    assert "body: { purchaseIntent: state.potionPurchaseIntent }" in application
    assert 'error?.code !== "duplicate_request"' in application
    assert "invalid_purchase_intent:" in application
    potion_section = application[
        application.index("function renderPotionDialog") :
        application.index("function installGameplayControls")
    ]
    assert "loadAvatar" not in potion_section
    assert ".potion-fab" in styles
    assert "var(--system-safe-left)" in styles
    assert "potionOutcomeUnknown" in application
    assert '"Check Habitica First"' in application
    assert "profileCoordinator.scheduleMutationRefresh(patch)" in potion_section
    assert "await loadProfile()" in potion_section
    assert 'throw Object.assign(new Error("Potion response was incomplete.")' not in application[
        application.index("async function submitPotion") :
        application.index("function installGameplayControls")
    ]


def test_habit_counters_use_authoritative_fields_and_accessible_period_labels():
    tasks = (ROOT / "static" / "miniapp" / "tasks.js").read_text()
    styles = (ROOT / "static" / "miniapp" / "app.css").read_text()

    assert "raw.counterUp" in tasks
    assert "raw.counterDown" in tasks
    assert "raw.counterFrequency" in tasks
    assert 'daily: "today"' in tasks
    assert 'weekly: "this week"' in tasks
    assert 'monthly: "this month"' in tasks
    assert 'counter.setAttribute("aria-label", summary.ariaLabel)' in tasks
    assert "${counterSummary.ariaLabel}" in tasks
    assert ".task-row__counter" in styles


def test_gameplay_snapshots_cannot_replace_a_full_first_load_profile():
    application = (ROOT / "static" / "miniapp" / "app.js").read_text()

    assert 'typeof envelope.profile.displayName === "string"' in application
    assert "if (!state.profilePayload && !isFullProfile) return false" in application
    assert "(payload?.invalidate?.profile === true || afterRefresh) && !appliedProfile" in application
    assert "await loadProfile()" in application
