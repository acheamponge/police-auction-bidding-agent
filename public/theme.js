// Theme before first paint: the stored choice wins; the OS setting is only
// the default when nothing is stored.
//
// This must be an external file loaded in <head> before the stylesheet —
// not an inline script. The app's Content-Security-Policy is
// `default-src 'self'` with no 'unsafe-inline', so an inline init script
// is blocked by the browser and never runs: the toggle would work for one
// page and reset on the next navigation. Same-origin scripts are allowed,
// so this file runs.
(function () {
  try {
    var stored = localStorage.getItem("paba-theme");
    if (stored === "dark" ||
        (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      document.documentElement.classList.add("dark");
    }
  } catch (error) {
    // Storage unavailable (private windows, blocked cookies): light default.
  }
})();
