// Docs site behavior: initialise Mermaid (theme-aware) and wire the sidebar filter.
// No framework, no network — everything is vendored so the site works offline.
(function () {
  "use strict";

  // Mermaid: match the OS colour scheme so diagrams read in light and dark.
  try {
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (window.mermaid) {
      window.mermaid.initialize({ startOnLoad: true, theme: dark ? "dark" : "default", securityLevel: "strict" });
    }
  } catch (e) { /* diagrams degrade to their source text */ }

  // Sidebar filter: hide entity links (and empty groups) that do not match the query.
  var filter = document.getElementById("filter");
  if (!filter) return;
  filter.addEventListener("input", function () {
    var q = filter.value.trim().toLowerCase();
    var groups = document.querySelectorAll("[data-group]");
    groups.forEach(function (group) {
      var links = group.querySelectorAll(".tree-link[data-name]");
      var anyVisible = false;
      links.forEach(function (a) {
        var match = !q || a.getAttribute("data-name").indexOf(q) !== -1;
        a.style.display = match ? "" : "none";
        if (match) anyVisible = true;
      });
      group.style.display = anyVisible ? "" : "none";
    });
  });
})();
