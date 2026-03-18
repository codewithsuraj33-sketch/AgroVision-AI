(function () {
  function initAdminImagePreview() {
    var fileInput = document.querySelector('input[name="image_file"]');
    var previewWrap = document.querySelector("[data-admin-image-preview-wrap]");
    var previewImg = document.querySelector("[data-admin-image-preview]");

    if (!fileInput || !previewWrap || !previewImg) {
      return;
    }

    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      if (!file) {
        previewWrap.hidden = true;
        previewImg.removeAttribute("src");
        return;
      }

      var url = URL.createObjectURL(file);
      previewImg.src = url;
      previewWrap.hidden = false;

      previewImg.onload = function () {
        try {
          URL.revokeObjectURL(url);
        } catch (e) {
          // Ignore revoke failures.
        }
      };
    });
  }

  function initAdminMobileMenu() {
    var body = document.body;
    var toggle = document.querySelector("[data-admin-menu-toggle]");
    var overlay = document.querySelector("[data-admin-menu-overlay]");
    var nav = document.querySelector("[data-admin-nav]");
    var icon = toggle ? toggle.querySelector("[data-admin-menu-icon]") : null;

    if (!body || !toggle || !overlay || !nav) {
      return;
    }

    if (toggle.dataset.adminMenuBound === "1") {
      return;
    }
    toggle.dataset.adminMenuBound = "1";

    function isOpen() {
      return body.classList.contains("admin-menu-open");
    }

    function syncIcon(open) {
      if (!icon) {
        return;
      }
      icon.className = open ? "fa-solid fa-xmark" : "fa-solid fa-bars";
      icon.setAttribute("aria-hidden", "true");
    }

    function setOpen(open) {
      if (window.innerWidth > 760) {
        open = false;
      }

      body.classList.toggle("admin-menu-open", open);
      nav.classList.toggle("is-open", open);
      overlay.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      syncIcon(open);
    }

    toggle.addEventListener("click", function (event) {
      if (window.innerWidth > 760) {
        setOpen(false);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setOpen(!isOpen());
    });

    overlay.addEventListener("click", function () {
      setOpen(false);
    });

    nav.addEventListener("click", function (event) {
      var link = event.target && event.target.closest ? event.target.closest("a") : null;
      if (link) {
        setOpen(false);
      }
    });

    document.addEventListener("click", function (event) {
      if (!isOpen()) {
        return;
      }
      if (nav.contains(event.target) || toggle.contains(event.target)) {
        return;
      }
      setOpen(false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth > 760) {
        setOpen(false);
      }
    });

    setOpen(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAdminMobileMenu();
      initAdminImagePreview();
    });
  } else {
    initAdminMobileMenu();
    initAdminImagePreview();
  }
})();
