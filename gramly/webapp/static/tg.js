/* Gramly UI runtime: Telegram controls the theme; no manual day/night switch. */
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp;
  var root = document.documentElement;
  var PALETTE = {
    light: { bg_color:"#ffffff", secondary_bg_color:"#f4f4f5", text_color:"#0f0f0f", hint_color:"#8d8d8d", link_color:"#3390ec", button_color:"#3390ec", button_text_color:"#ffffff", section_bg_color:"#ffffff", section_header_text_color:"#6d6d72", subtitle_text_color:"#707579", section_separator_color:"rgba(0,0,0,0.08)", destructive_text_color:"#e53935" },
    dark: { bg_color:"#17212b", secondary_bg_color:"#0e1621", text_color:"#f5f5f5", hint_color:"#708499", link_color:"#6ab3f3", button_color:"#5288c1", button_text_color:"#ffffff", section_bg_color:"#17212b", section_header_text_color:"#6ab3f3", subtitle_text_color:"#708499", section_separator_color:"rgba(255,255,255,0.09)", destructive_text_color:"#ec3942" }
  };
  function scheme() {
    if (tg && tg.colorScheme) return tg.colorScheme === "dark" ? "dark" : "light";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function apply() {
    var s = scheme();
    root.setAttribute("data-scheme", s);
    root.style.colorScheme = s;
    var params = tg && tg.themeParams && Object.keys(tg.themeParams).length ? tg.themeParams : PALETTE[s];
    Object.keys(params).forEach(function (k) { if (params[k]) root.style.setProperty("--tg-theme-" + k.replace(/_/g, "-"), params[k]); });
    try {
      if (tg && tg.setHeaderColor) tg.setHeaderColor("bg_color");
      if (tg && tg.setBackgroundColor) tg.setBackgroundColor("bg_color");
      if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast("7.10") && tg.setBottomBarColor) tg.setBottomBarColor("bg_color");
    } catch (e) {}
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", s === "dark" ? "#17212b" : "#ffffff");
  }
  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "select") tg.HapticFeedback.selectionChanged();
      else if (kind === "success" || kind === "error") tg.HapticFeedback.notificationOccurred(kind);
      else tg.HapticFeedback.impactOccurred(kind === "heavy" ? "heavy" : "light");
    } catch (e) {}
  }
  function toast(text) {
    var el = document.createElement("div"); el.className = "sticky-note"; el.textContent = text; document.body.appendChild(el);
    requestAnimationFrame(function () { el.classList.add("show"); });
    setTimeout(function () { el.classList.remove("show"); setTimeout(function () { el.remove(); }, 250); }, 1900);
  }
  function api(path, body) {
    var opts = { method: body ? "POST" : "GET", headers: { "X-Requested-With": "Gramly" } };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      var meta = document.querySelector('meta[name="csrf-token"]');
      if (meta && meta.content) { opts.headers["X-CSRF-Token"] = meta.content; body._csrf = meta.content; }
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      if (r.status === 400 || r.status === 401) return Promise.reject(new Error(r.status === 401 ? "unauthorized" : "csrf"));
      return r.json().catch(function () { return {}; });
    });
  }
  function post(path, body) { return api(path, body || {}); }
  function init() {
    apply();
    if (tg) { try { tg.ready(); } catch (e) {} try { tg.expand(); } catch (e) {} if (tg.onEvent) tg.onEvent("themeChanged", apply); }
    if (window.matchMedia) { var mq = window.matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener && mq.addEventListener("change", apply); }
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-haptic]"); if (t) haptic(t.getAttribute("data-haptic"));
      var copy = ev.target.closest("[data-copy]");
      if (copy) {
        var v = copy.getAttribute("data-copy");
        (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).then(function () { toast("Скопировано"); haptic("success"); }).catch(function () { toast("Не удалось скопировать"); });
      }
    });
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest('a[href^="http"]'); if (a && tg && tg.openLink) { ev.preventDefault(); tg.openLink(a.href); }
    });
    var search = document.querySelector("[data-search]");
    if (search) {
      var timer = null;
      search.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { var q = search.value.trim(); var url = new URL(window.location.href); if (q) url.searchParams.set("q", q); else url.searchParams.delete("q"); window.location.href = url.toString(); }, 420); });
    }
    window.Gramly = { api: api, post: post, toast: toast, haptic: haptic, tg: tg, applyTheme: apply };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
