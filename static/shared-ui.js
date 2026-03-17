(function () {
  const shellId = "av-translate-shell";
  const hostId = "av-google-translate-host";
  const panelAttr = "data-av-translate-panel";
  const toggleAttr = "data-av-translate-toggle";
  const labelAttr = "data-av-translate-current";
  const buttonAttr = "data-av-translate-option";
  const storageKey = "av-translate-language";
  const languages = [
    { code: "en", short: "EN", label: "English", native: "English" },
    { code: "hi", short: "HI", label: "Hindi", native: "Hindi" },
    { code: "or", short: "OR", label: "Odia", native: "Odia" },
  ];
  let widgetPromise = null;

  function getCurrentLanguage() {
    const cookieMatch = document.cookie.match(/(?:^|;\s*)googtrans=\/[^/]+\/([^;]+)/i);
    if (cookieMatch && cookieMatch[1]) {
      return decodeURIComponent(cookieMatch[1]).toLowerCase();
    }

    const stored = (window.localStorage && localStorage.getItem(storageKey)) || "en";
    return stored.toLowerCase();
  }

  function setStoredLanguage(code) {
    if (!window.localStorage) {
      return;
    }
    localStorage.setItem(storageKey, code);
  }

  function setGoogTransCookie(code) {
    const value = `/en/${code}`;
    document.cookie = `googtrans=${value}; path=/`;

    const host = window.location.hostname;
    if (!host) {
      return;
    }

    try {
      document.cookie = `googtrans=${value}; path=/; domain=${host}`;
    } catch (error) {
      // Ignore invalid domain writes on localhost-style hosts.
    }
  }

  function buildShell() {
    let shell = document.getElementById(shellId);
    if (shell) {
      return shell;
    }

    shell = document.createElement("section");
    shell.id = shellId;
    shell.className = "av-translate-shell notranslate";
    shell.setAttribute("aria-label", "Translate this page");
    shell.innerHTML = [
      `<button class="av-translate-toggle" type="button" ${toggleAttr} aria-expanded="false" aria-controls="av-translate-panel">`,
      '  <span class="av-translate-toggle-icon" aria-hidden="true"><i class="fas fa-language"></i></span>',
      '  <span class="av-translate-toggle-copy"><strong>Translate</strong><span>English, Hindi, Odia</span></span>',
      `  <span class="av-translate-current" ${labelAttr}>EN</span>`,
      "</button>",
      `<div class="av-translate-panel" id="av-translate-panel" ${panelAttr} hidden>`,
      '  <div class="av-translate-panel-head">',
      "    <strong>Select language</strong>",
      "    <span>Choose one option and the page will switch to that language.</span>",
      "  </div>",
      '  <div class="av-translate-options" role="list"></div>',
      '  <span class="av-translate-status">Translation ready</span>',
      "</div>",
    ].join("");

    document.body.appendChild(shell);
    const optionsRoot = shell.querySelector(".av-translate-options");
    optionsRoot.innerHTML = languages
      .map(function (language) {
        return [
          `<button class="av-translate-option" type="button" ${buttonAttr} data-lang-code="${language.code}">`,
          `  <span class="av-translate-option-label">${language.label}</span>`,
          `  <span class="av-translate-option-native">${language.native}</span>`,
          "</button>",
        ].join("");
      })
      .join("");

    return shell;
  }

  function getShellParts() {
    const shell = buildShell();
    return {
      shell,
      toggle: shell.querySelector(`[${toggleAttr}]`),
      panel: shell.querySelector(`[${panelAttr}]`),
      label: shell.querySelector(`[${labelAttr}]`),
      buttons: Array.from(shell.querySelectorAll(`[${buttonAttr}]`)),
    };
  }

  function closePanel() {
    const { shell, panel, toggle } = getShellParts();
    shell.classList.remove("is-open");
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function openPanel() {
    const { shell, panel, toggle } = getShellParts();
    shell.classList.add("is-open");
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    ensureGoogleWidget();
  }

  function updateCurrentLabel(code) {
    const currentCode = (code || getCurrentLanguage() || "en").toLowerCase();
    const selected = languages.find(function (language) {
      return language.code === currentCode;
    }) || languages[0];
    const { shell, label, buttons } = getShellParts();

    if (label) {
      label.textContent = selected.short;
      label.title = selected.label;
    }

    if (shell) {
      shell.dataset.language = selected.code;
    }

    buttons.forEach(function (button) {
      const isActive = button.dataset.langCode === selected.code;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function ensureHiddenHost() {
    let host = document.getElementById(hostId);
    if (host) {
      return host;
    }

    host = document.createElement("div");
    host.id = hostId;
    host.className = "av-translate-host notranslate";
    host.setAttribute("aria-hidden", "true");
    document.body.appendChild(host);
    return host;
  }

  function getGoogleCombo() {
    return document.querySelector(".goog-te-combo");
  }

  function initializeGoogleWidget() {
    if (!window.google || !google.translate || !google.translate.TranslateElement) {
      return;
    }

    const host = ensureHiddenHost();
    if (host.dataset.initialized === "1" || getGoogleCombo()) {
      return;
    }

    host.dataset.initialized = "1";
    new google.translate.TranslateElement(
      {
        pageLanguage: "en",
        includedLanguages: "en,hi,or",
        autoDisplay: false,
        layout: google.translate.TranslateElement.InlineLayout.SIMPLE,
      },
      hostId
    );
  }

  function waitForCombo() {
    return new Promise(function (resolve, reject) {
      let attempts = 0;
      const interval = window.setInterval(function () {
        attempts += 1;
        const combo = getGoogleCombo();
        if (combo) {
          window.clearInterval(interval);
          resolve(combo);
          return;
        }

        if (window.google && google.translate && google.translate.TranslateElement) {
          initializeGoogleWidget();
        }

        if (attempts > 60) {
          window.clearInterval(interval);
          reject(new Error("Google Translate widget not available"));
        }
      }, 250);
    });
  }

  function loadGoogleTranslateScript() {
    const existingScript = Array.from(document.scripts).find(function (script) {
      return script.src && script.src.indexOf("translate_a/element.js") !== -1;
    });

    if (existingScript) {
      return;
    }

    window.avSharedTranslateInit = function () {
      initializeGoogleWidget();
    };

    const script = document.createElement("script");
    script.src = "//translate.google.com/translate_a/element.js?cb=avSharedTranslateInit";
    script.async = true;
    document.body.appendChild(script);
  }

  function ensureGoogleWidget() {
    if (widgetPromise) {
      return widgetPromise;
    }

    const existingCombo = getGoogleCombo();
    if (existingCombo) {
      widgetPromise = Promise.resolve(existingCombo);
      return widgetPromise;
    }

    if (window.google && google.translate && google.translate.TranslateElement) {
      initializeGoogleWidget();
      widgetPromise = waitForCombo();
      return widgetPromise;
    }

    loadGoogleTranslateScript();
    widgetPromise = waitForCombo();
    return widgetPromise;
  }

  function applyLanguage(code) {
    const targetCode = (code || "en").toLowerCase();
    const currentCode = getCurrentLanguage();
    
    if (targetCode === currentCode) {
      closePanel();
      return;
    }

    setStoredLanguage(targetCode);
    setGoogTransCookie(targetCode);
    updateCurrentLabel(targetCode);
    closePanel();

    ensureGoogleWidget()
      .then(function (combo) {
        if (!combo) {
          window.location.reload();
          return;
        }

        combo.value = targetCode;
        combo.dispatchEvent(new Event("change"));
        
        // Force reload after a short delay to ensure cookie is respected by Google Translate on next load/navigation
        window.setTimeout(function () {
          window.location.reload();
        }, 150);
      })
      .catch(function () {
        window.location.reload();
      });
  }

  function bindEvents() {
    const { shell, toggle, buttons } = getShellParts();
    if (!toggle || toggle.dataset.bound === "1") {
      return;
    }

    toggle.dataset.bound = "1";

    toggle.addEventListener("click", function () {
      if (shell.classList.contains("is-open")) {
        closePanel();
      } else {
        openPanel();
      }
    });

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        applyLanguage(button.dataset.langCode);
      });
    });

    document.addEventListener("click", function (event) {
      if (shell.classList.contains("is-open") && !shell.contains(event.target)) {
        closePanel();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closePanel();
      }
    });
  }

  function syncInitialLanguage() {
    const current = getCurrentLanguage();
    updateCurrentLabel(current);

    if (current !== "en") {
      ensureGoogleWidget()
        .then(function (combo) {
          if (combo.value !== current) {
            combo.value = current;
            combo.dispatchEvent(new Event("change"));
          }
        })
        .catch(function () {
          // Ignore initialization errors; the button remains visible.
        });
    }
  }

  function init() {
    buildShell();
    bindEvents();
    syncInitialLanguage();
    window.setInterval(function () {
      updateCurrentLabel(getCurrentLanguage());
    }, 1200);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
