(() => {
  "use strict";

  const key = "hh_theme_mode";
  const allowed = new Set(["auto", "light", "dark"]);
  let mode = "auto";
  try {
    const saved = window.localStorage.getItem(key);
    if (allowed.has(saved)) mode = saved;
  } catch (_error) {
    // Storage can be disabled in privacy-focused WebViews.
  }

  const webApp = window.Telegram?.WebApp;
  const inTelegram = Boolean(webApp?.initData) && webApp?.platform !== "unknown";
  const telegramScheme = inTelegram ? webApp.colorScheme : null;
  const systemDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const effective = mode === "auto"
    ? (telegramScheme === "dark" || (!telegramScheme && systemDark) ? "dark" : "light")
    : mode;
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.dataset.theme = effective;
  document.documentElement.style.colorScheme = effective;
})();
