// Applied before first paint so a saved light/dark preference never flashes.
(function () {
  try {
    var theme = localStorage.getItem("dbs.theme");
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  } catch (e) {
    /* storage unavailable: follow the system preference */
  }
})();
