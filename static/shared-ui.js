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

(function () {
  function shouldSkipSharedUi() {
    if (document.body.classList.contains("admin-page")) {
      return true;
    }
    return !document.body.classList.contains("dashboard-page");
  }

  function getPageConfig() {
    var path = window.location.pathname || "/";
    if (path === "/farms") {
      return { label: "Add Farm", href: "#add-farm-form", detail: "New plot add karke dashboard sync karo." };
    }
    if (path === "/market") {
      return { label: "Buy Product", href: "#storeGrid", detail: "Diagnosis tags ke basis par right product shortlist karo." };
    }
    if (path === "/disease-detection") {
      return { label: "Detect Disease", href: "#crop-image", detail: "Leaf photo upload karke AI diagnosis start karo." };
    }
    if (path === "/refer-and-earn") {
      return { label: "Share Referral", href: "#referralShareCard", detail: "Referral code ko fast share karke rewards unlock karo." };
    }
    return null;
  }

  function ensurePrimaryActionBanner() {
    var config = getPageConfig();
    if (!config || document.querySelector(".shared-primary-action")) {
      return;
    }

    var host = document.querySelector(".dashboard-hero-copy, .store-hero-copy, .settings-hero .dashboard-hero-copy");
    if (!host) {
      host = document.querySelector(".dashboard-main");
    }
    if (!host) {
      return;
    }

    var action = document.createElement("div");
    action.className = "shared-primary-action";
    action.innerHTML = [
      '<div class="shared-primary-action-copy">',
      "  <strong>Primary action</strong>",
      "  <span>" + config.detail + "</span>",
      "</div>",
      '<a class="shared-primary-action-button" href="' + config.href + '">' + config.label + "</a>",
    ].join("");
    host.appendChild(action);
  }

  function createChatShell() {
    if (document.getElementById("aiCropDoctorLauncher")) {
      return;
    }

    var shell = document.createElement("section");
    shell.className = "ai-crop-doctor-shell";
    shell.innerHTML = [
      '<button id="aiCropDoctorLauncher" class="ai-crop-doctor-launcher" type="button" aria-expanded="false" aria-controls="aiCropDoctorPanel" aria-label="Open AI Crop Doctor" title="AI Crop Doctor">',
      '  <span class="ai-crop-doctor-launcher-icon"><i class="fas fa-user-doctor"></i><span class="ai-crop-doctor-launcher-accent"><i class="fas fa-leaf"></i></span></span>',
      '  <span class="ai-crop-doctor-launcher-badge">AI</span>',
      '  <span class="sr-only">AI Crop Doctor</span>',
      "</button>",
      '<section id="aiCropDoctorPanel" class="ai-crop-doctor-panel" hidden aria-label="AI Crop Doctor chat">',
      '  <header class="ai-crop-doctor-head">',
      '    <div><strong>AI Crop Doctor Chat</strong><span>Farmer-friendly Hinglish help</span></div>',
      '    <button class="ai-crop-doctor-close" type="button" aria-label="Close AI Crop Doctor"><i class="fas fa-times"></i></button>',
      "  </header>",
      '  <div class="ai-crop-doctor-suggestions">',
      '    <button type="button" data-ai-chat-suggestion="Mere tomato me patta murjha raha hai kya karu?">Tomato patta murjha raha hai</button>',
      '    <button type="button" data-ai-chat-suggestion="Gehun me yellow rust ke liye abhi kya step loon?">Yellow rust next step</button>',
      '    <button type="button" data-ai-chat-suggestion="Kal barish ho to spray postpone karna chahiye kya?">Spray postpone karu?</button>',
      "  </div>",
      '  <div class="ai-crop-doctor-messages" id="aiCropDoctorMessages" aria-live="polite"></div>',
      '  <form class="ai-crop-doctor-form" id="aiCropDoctorForm">',
      '    <label class="sr-only" for="aiCropDoctorInput">Ask AI Crop Doctor</label>',
      '    <input id="aiCropDoctorInput" type="text" placeholder="Jaise: Mere tomato me patta murjha raha hai..." autocomplete="off">',
      '    <button type="button" class="ai-crop-doctor-voice" id="aiCropDoctorVoice" aria-label="Speak your question"><i class="fas fa-microphone"></i></button>',
      '    <button type="submit" class="ai-crop-doctor-send" aria-label="Send question"><i class="fas fa-paper-plane"></i></button>',
      "  </form>",
      '  <p class="ai-crop-doctor-status" id="aiCropDoctorStatus">Voice optional hai. Type karke bhi pooch sakte ho.</p>',
      "</section>",
    ].join("");
    document.body.appendChild(shell);
  }

  function appendMessage(messagesRoot, type, text) {
    if (!messagesRoot) {
      return;
    }
    var item = document.createElement("article");
    item.className = "ai-crop-doctor-message ai-crop-doctor-message-" + type;
    item.textContent = text;
    messagesRoot.appendChild(item);
    messagesRoot.scrollTop = messagesRoot.scrollHeight;
  }

  function speakText(text) {
    if (!("speechSynthesis" in window) || !text) {
      return;
    }
    var utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "hi-IN";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  }

  function initAiCropDoctor() {
    if (shouldSkipSharedUi()) {
      return;
    }

    createChatShell();
    var shell = document.querySelector(".ai-crop-doctor-shell");
    var launcher = document.getElementById("aiCropDoctorLauncher");
    var panel = document.getElementById("aiCropDoctorPanel");
    var closeButton = document.querySelector(".ai-crop-doctor-close");
    var form = document.getElementById("aiCropDoctorForm");
    var input = document.getElementById("aiCropDoctorInput");
    var messages = document.getElementById("aiCropDoctorMessages");
    var status = document.getElementById("aiCropDoctorStatus");
    var voiceButton = document.getElementById("aiCropDoctorVoice");
    var recognition = null;

    if (!shell || !launcher || !panel || !form || !input || !messages) {
      return;
    }

    if (!messages.children.length) {
      appendMessage(messages, "assistant", "Namaste. Main AI Crop Doctor hoon. Aap Hinglish me crop, disease, weather, ya spray timing pooch sakte ho.");
    }

    function collectConversationHistory() {
      return Array.prototype.slice.call(messages.querySelectorAll(".ai-crop-doctor-message"))
        .slice(-6)
        .map(function (item) {
          return {
            role: item.classList.contains("ai-crop-doctor-message-user") ? "user" : "assistant",
            content: (item.textContent || "").trim(),
          };
        })
        .filter(function (item) {
          return item.content;
        });
    }

    function setOpen(open) {
      panel.hidden = !open;
      launcher.hidden = !!open;
      launcher.setAttribute("aria-expanded", open ? "true" : "false");
      shell.classList.toggle("is-open", !!open);
      document.body.classList.toggle("ai-crop-doctor-open", !!open);
      if (open) {
        window.setTimeout(function () {
          input.focus();
        }, 40);
      }
    }

    setOpen(false);

    async function sendQuery(text) {
      var query = (text || "").trim();
      if (!query) {
        return;
      }

      appendMessage(messages, "user", query);
      input.value = "";
      status.textContent = "AI Crop Doctor soch raha hai...";

      try {
        var response = await fetch("/api/ai-chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: query, history: collectConversationHistory() }),
        });
        var data = await response.json();
        if (!response.ok || !data.success) {
          throw new Error(data.error || "Reply nahi aa paya.");
        }
        appendMessage(messages, "assistant", data.response || "Koi reply generate nahi hua.");
        if (data.provider === "groq") {
          status.textContent = "Groq AI ne aapke context ke saath jawab diya.";
        } else if (data.provider === "fallback") {
          status.textContent = "AI Crop Doctor ne local expert mode me jawab diya.";
        } else {
          status.textContent = "Voice optional hai. Type karke bhi pooch sakte ho.";
        }
        speakText(data.response || "");
      } catch (error) {
        appendMessage(messages, "assistant", "Abhi network ya assistant issue aa raha hai. Thodi der baad phir try karo.");
        status.textContent = error.message || "Assistant unavailable";
      }
    }

    launcher.addEventListener("click", function () {
      setOpen(panel.hidden);
    });
    closeButton && closeButton.addEventListener("click", function () {
      setOpen(false);
    });
    document.addEventListener("click", function (event) {
      if (panel.hidden) {
        return;
      }
      if (panel.contains(event.target) || launcher.contains(event.target)) {
        return;
      }
      setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !panel.hidden) {
        setOpen(false);
      }
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      sendQuery(input.value);
    });

    document.querySelectorAll("[data-ai-chat-suggestion]").forEach(function (button) {
      button.addEventListener("click", function () {
        setOpen(true);
        sendQuery(button.getAttribute("data-ai-chat-suggestion") || "");
      });
    });

    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition && voiceButton) {
      recognition = new SpeechRecognition();
      recognition.lang = "hi-IN";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      recognition.addEventListener("result", function (event) {
        var transcript = event.results && event.results[0] && event.results[0][0] ? event.results[0][0].transcript : "";
        input.value = transcript;
        status.textContent = "Voice input mil gaya. Sending question...";
        sendQuery(transcript);
      });
      recognition.addEventListener("start", function () {
        voiceButton.classList.add("is-listening");
        status.textContent = "Bolna start karo...";
      });
      recognition.addEventListener("end", function () {
        voiceButton.classList.remove("is-listening");
      });
      recognition.addEventListener("error", function () {
        voiceButton.classList.remove("is-listening");
        status.textContent = "Voice input device/browser me available nahi hai. Type karke pooch lo.";
      });

      voiceButton.addEventListener("click", function () {
        setOpen(true);
        recognition.start();
      });
    } else if (voiceButton) {
      voiceButton.disabled = true;
      voiceButton.title = "Voice support is not available in this browser.";
    }
  }

  function init() {
    if (shouldSkipSharedUi()) {
      return;
    }
    ensurePrimaryActionBanner();
    initAiCropDoctor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
