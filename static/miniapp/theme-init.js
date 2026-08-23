(() => {
  "use strict";

  const key = "hh_theme_mode";
  const displayScaleKey = "hh_display_scale";
  const allowed = new Set(["auto", "light", "dark"]);
  const allowedDisplayScales = new Set(["0.8", "0.9", "1", "1.1", "1.2"]);
  let mode = "auto";
  let displayScale = "1";
  try {
    const saved = window.localStorage.getItem(key);
    if (allowed.has(saved)) mode = saved;
    const savedDisplayScale = window.localStorage.getItem(displayScaleKey);
    if (allowedDisplayScales.has(savedDisplayScale)) displayScale = savedDisplayScale;
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
  document.documentElement.dataset.displayScale = displayScale;
  document.documentElement.style.setProperty("--display-scale", displayScale);
  document.documentElement.style.setProperty("--minimum-layout-width", `${280 / Number(displayScale)}px`);
  document.documentElement.style.zoom = displayScale;
  document.documentElement.style.colorScheme = effective;
})();
