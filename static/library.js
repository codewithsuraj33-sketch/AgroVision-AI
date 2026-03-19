(function () {
  function bindImages(root) {
    const scope = root || document;
    scope.querySelectorAll("img[data-fallback-src]").forEach(function (img) {
      if (img.dataset.bound === "1") return;
      img.dataset.bound = "1";

      const card = img.closest(".library-dcard");
      if (card) {
        card.classList.add("is-loading");
      }

      img.addEventListener("load", function () {
        if (card) card.classList.remove("is-loading");
      });
      img.addEventListener("error", function () {
        const fallback = img.getAttribute("data-fallback-src");
        if (fallback && img.src !== fallback) {
          img.src = fallback;
        }
        if (card) card.classList.remove("is-loading");
      });
    });
  }

  function initCropFilter() {
    const select = document.querySelector("[data-library-crop-filter]");
    if (!select) return;
    select.addEventListener("change", function () {
      const next = new URL(window.location.href);
      if (select.value && select.value !== "All") {
        next.searchParams.set("crop", select.value);
      } else {
        next.searchParams.delete("crop");
      }
      window.location.href = next.toString();
    });
  }

  function initTabs() {
    const tabs = Array.from(document.querySelectorAll("[data-library-tab]"));
    if (!tabs.length) return;
    const panels = Array.from(document.querySelectorAll("[data-library-panel]"));

    function setActive(key) {
      tabs.forEach(function (btn) {
        btn.classList.toggle("is-active", btn.dataset.libraryTab === key);
      });
      panels.forEach(function (panel) {
        panel.hidden = panel.dataset.libraryPanel !== key;
      });
    }

    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        setActive(btn.dataset.libraryTab || "");
      });
    });

    setActive(tabs[0].dataset.libraryTab || "");
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindImages(document);
    initCropFilter();
    initTabs();
  });
})();

