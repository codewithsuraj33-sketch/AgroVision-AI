(function () {
  function initMobileSidebar() {
    var body = document.body;
    var root = document.documentElement;
    var toggle = document.getElementById("mobileMenuToggle");
    var sidebar = document.querySelector(".dashboard-sidebar");
    if (!body || !toggle || !sidebar) {
      return;
    }

    if (sidebar.dataset.avMobileSidebarBound === "1") {
      return;
    }
    sidebar.dataset.avMobileSidebarBound = "1";

    toggle.setAttribute("aria-expanded", "false");
    if (!toggle.getAttribute("aria-label")) {
      toggle.setAttribute("aria-label", "Open menu");
    }

    var overlay = document.querySelector("[data-av-mobile-sidebar-overlay]");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "mobile-sidebar-overlay";
      overlay.setAttribute("data-av-mobile-sidebar-overlay", "1");
      overlay.hidden = true;
      document.body.appendChild(overlay);
    }

    function isMobile() {
      return window.innerWidth <= 780;
    }

    function isOpen() {
      return sidebar.classList.contains("sidebar-open");
    }

    function setOpen(open) {
      if (!isMobile()) {
        open = false;
      }

      sidebar.classList.toggle("sidebar-open", !!open);
      body.classList.toggle("mobile-sidebar-open", !!open);
      root.classList.toggle("mobile-sidebar-open", !!open);
      overlay.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    toggle.addEventListener(
      "click",
      function (event) {
        if (!isMobile()) {
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        event.stopPropagation();
        setOpen(!isOpen());
      },
      true
    );

    sidebar.addEventListener("click", function (event) {
      var link = event.target && event.target.closest ? event.target.closest("a") : null;
      if (link && isMobile()) {
        setOpen(false);
      }
    });

    overlay.addEventListener("click", function () {
      setOpen(false);
    });

    document.addEventListener(
      "click",
      function (event) {
        if (!isMobile() || !isOpen()) {
          return;
        }
        if (sidebar.contains(event.target) || toggle.contains(event.target)) {
          return;
        }
        setOpen(false);
      },
      true
    );

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });

    window.addEventListener("resize", function () {
      if (!isMobile()) {
        setOpen(false);
      }
    });

    window.addEventListener("pageshow", function () {
      setOpen(false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMobileSidebar);
  } else {
    initMobileSidebar();
  }
})();
