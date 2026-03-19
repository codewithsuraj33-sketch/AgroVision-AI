(function () {
  const fallbackImage = "/static/images/store-product-fallback.svg";
  let razorpayScriptPromise = null;
  let recommendationFocused = false;

  function getCookie(name) {
    const needle = String(name || "") + "=";
    const parts = String(document.cookie || "").split(";");
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i].trim();
      if (!part) continue;
      if (part.indexOf(needle) === 0) {
        return decodeURIComponent(part.slice(needle.length));
      }
    }
    return "";
  }

  function getCsrfToken() {
    return getCookie("csrf_token");
  }

  function createToastRoot() {
    let toastRoot = document.getElementById("storeToast");
    if (toastRoot) {
      return toastRoot;
    }

    toastRoot = document.createElement("div");
    toastRoot.id = "storeToast";
    toastRoot.className = "store-toast";
    document.body.appendChild(toastRoot);
    return toastRoot;
  }

  function showToast(message, type) {
    const toastRoot = createToastRoot();
    toastRoot.textContent = message || "Action completed.";
    toastRoot.dataset.type = type || "success";
    toastRoot.classList.add("is-visible");

    window.clearTimeout(showToast._timer);
    showToast._timer = window.setTimeout(function () {
      toastRoot.classList.remove("is-visible");
    }, 3200);
  }

  function loadRazorpayScript() {
    if (window.Razorpay) {
      return Promise.resolve(window.Razorpay);
    }

    if (razorpayScriptPromise) {
      return razorpayScriptPromise;
    }

    razorpayScriptPromise = new Promise(function (resolve, reject) {
      const existingScript = Array.from(document.scripts).find(function (script) {
        return script.src && script.src.indexOf("checkout.razorpay.com/v1/checkout.js") !== -1;
      });

      if (existingScript) {
        existingScript.addEventListener("load", function () {
          resolve(window.Razorpay);
        });
        existingScript.addEventListener("error", function () {
          reject(new Error("Razorpay script failed to load."));
        });
        return;
      }

      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.async = true;
      script.onload = function () {
        resolve(window.Razorpay);
      };
      script.onerror = function () {
        reject(new Error("Razorpay script failed to load."));
      };
      document.body.appendChild(script);
    });

    return razorpayScriptPromise;
  }

  function findLoadingShell(image) {
    return image.closest(".store-card, .store-detail-media, .disease-store-card");
  }

  function markShellLoaded(image) {
    const shell = findLoadingShell(image);
    if (shell) {
      shell.classList.remove("is-loading");
    }
  }

  function bindStoreImages(scope) {
    const root = scope || document;
    root.querySelectorAll("img[data-fallback-src]").forEach(function (image) {
      if (image.dataset.storeImageBound === "1") {
        return;
      }

      image.dataset.storeImageBound = "1";

      function applyFallback() {
        if (image.dataset.fallbackApplied === "1") {
          markShellLoaded(image);
          return;
        }

        image.dataset.fallbackApplied = "1";
        image.classList.add("is-fallback");
        image.src = image.dataset.fallbackSrc || fallbackImage;
        markShellLoaded(image);
      }

      image.addEventListener("load", function () {
        const fallbackSrc = image.dataset.fallbackSrc || fallbackImage;
        if ((image.currentSrc || image.src || "").indexOf(fallbackSrc) !== -1) {
          image.classList.add("is-fallback");
        }
        markShellLoaded(image);
      });

      image.addEventListener("error", applyFallback);

      if (image.complete) {
        if (image.naturalWidth > 0) {
          markShellLoaded(image);
        } else {
          applyFallback();
        }
      }
    });
  }

  function setBuyButtonLoading(button, isLoading) {
    if (!button) {
      return;
    }

    if (isLoading) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = "Processing...";
      button.disabled = true;
      button.classList.add("is-loading");
      return;
    }

    button.textContent = button.dataset.originalLabel || "Buy Now";
    button.disabled = false;
    button.classList.remove("is-loading");
  }

  async function finalizePayment(payload, button) {
    const csrfToken = getCsrfToken();
    const response = await fetch("/api/store/payment-success", {
      method: "POST",
      headers: Object.assign(
        { "Content-Type": "application/json" },
        csrfToken ? { "X-CSRFToken": csrfToken } : {}
      ),
      body: JSON.stringify(payload),
    });
    const data = await response.json();

    setBuyButtonLoading(button, false);

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Payment confirmation failed.");
    }

    showToast(data.message || "Payment saved successfully.", data.verified ? "success" : "info");
    return data;
  }

  async function simulateSuccessfulCheckout(checkoutData, button, paymentResponse) {
    const simulatedResponse = paymentResponse || {
      razorpay_order_id: checkoutData.checkout && checkoutData.checkout.order_id ? checkoutData.checkout.order_id : "",
      razorpay_payment_id: "demo_pay_" + Date.now(),
      razorpay_signature: "",
    };

    return finalizePayment(
      {
        product_id: checkoutData.product.id,
        order_record_id: checkoutData.order_record_id,
        source: button.dataset.buySource || "store",
        checkout_mode: checkoutData.checkout_mode || "demo",
        razorpay_order_id: simulatedResponse.razorpay_order_id,
        razorpay_payment_id: simulatedResponse.razorpay_payment_id,
        razorpay_signature: simulatedResponse.razorpay_signature,
      },
      button
    );
  }

  async function startCheckout(button) {
    const productId = button.dataset.buyProduct;
    if (!productId) {
      showToast("Product information is missing.", "error");
      return;
    }

    setBuyButtonLoading(button, true);

    try {
      const csrfToken = getCsrfToken();
      const response = await fetch("/api/store/checkout", {
        method: "POST",
        headers: Object.assign(
          { "Content-Type": "application/json" },
          csrfToken ? { "X-CSRFToken": csrfToken } : {}
        ),
        body: JSON.stringify({
          product_id: Number(productId),
          source: button.dataset.buySource || "store",
        }),
      });
      const checkoutData = await response.json();

      if (!response.ok || !checkoutData.success) {
        throw new Error(checkoutData.error || "Checkout could not be started.");
      }

      const options = Object.assign({}, checkoutData.checkout || {});
      options.handler = function (paymentResponse) {
        simulateSuccessfulCheckout(checkoutData, button, paymentResponse).catch(function (error) {
          showToast(error.message || "Payment save failed.", "error");
        });
      };
      options.modal = {
        ondismiss: function () {
          setBuyButtonLoading(button, false);
        },
      };

      try {
        await loadRazorpayScript();
        if (window.Razorpay) {
          const checkout = new window.Razorpay(options);
          checkout.on("payment.failed", function () {
            setBuyButtonLoading(button, false);
            showToast("Payment was not completed.", "error");
          });
          checkout.open();
          return;
        }
      } catch (error) {
        console.warn(error);
      }

      await simulateSuccessfulCheckout(checkoutData, button);
    } catch (error) {
      setBuyButtonLoading(button, false);
      showToast(error.message || "Checkout failed.", "error");
    }
  }

  function initStoreCatalog() {
    const root = document.querySelector("[data-store-page]");
    if (!root) {
      return;
    }

    const grid = document.getElementById("storeGrid");
    const cards = Array.from(grid ? grid.querySelectorAll(".store-card") : []);
    const searchInput = document.getElementById("storeSearch");
    const sortSelect = document.getElementById("storeSort");
    const categoryInput = document.getElementById("storeCategoryInput");
    const useCaseInput = document.getElementById("storeUseCaseInput");
    const emptyState = document.getElementById("storeEmptyState");
    const countLabel = document.getElementById("storeResultsCount");
    const filterForm = document.getElementById("storeFilterForm");
    const tabs = Array.from(document.querySelectorAll("[data-store-category]"));
    const useCaseTabs = Array.from(document.querySelectorAll("[data-store-use-case]"));
    const initialCategory = root.dataset.activeCategory || "All";
    const initialUseCase = root.dataset.activeUseCase || "";
    const initialQuery = root.dataset.searchQuery || "";
    const initialSort = root.dataset.sortOption || "featured";
    const recommendedSlug = root.dataset.recommendedSlug || "";

    if (searchInput) {
      searchInput.value = initialQuery;
    }
    if (sortSelect) {
      sortSelect.value = initialSort;
    }
    if (categoryInput) {
      categoryInput.value = initialCategory;
    }
    if (useCaseInput) {
      useCaseInput.value = initialUseCase;
    }

    function updateUrl(query, category, sort, useCase) {
      const nextUrl = new URL(window.location.href);
      if (query) {
        nextUrl.searchParams.set("q", query);
      } else {
        nextUrl.searchParams.delete("q");
      }

      if (category && category !== "All") {
        nextUrl.searchParams.set("category", category);
      } else {
        nextUrl.searchParams.delete("category");
      }

      if (sort && sort !== "featured") {
        nextUrl.searchParams.set("sort", sort);
      } else {
        nextUrl.searchParams.delete("sort");
      }

      if (useCase) {
        nextUrl.searchParams.set("use_case", useCase);
      } else {
        nextUrl.searchParams.delete("use_case");
      }

      if (recommendedSlug) {
        nextUrl.searchParams.set("recommended", recommendedSlug);
      }

      window.history.replaceState({}, "", nextUrl.toString());
    }

    function setActiveTab(category) {
      tabs.forEach(function (tab) {
        const isActive = tab.dataset.storeCategory === category;
        tab.classList.toggle("is-active", isActive);
      });
      if (categoryInput) {
        categoryInput.value = category;
      }
    }

    function setActiveUseCase(useCase) {
      useCaseTabs.forEach(function (tab) {
        const isActive = tab.dataset.storeUseCase === useCase;
        tab.classList.toggle("is-active", isActive);
      });
      if (useCaseInput) {
        useCaseInput.value = useCase;
      }
    }

    function sortCards(visibleCards, sortOption) {
      visibleCards.sort(function (left, right) {
        const leftSlug = left.dataset.productSlug || "";
        const rightSlug = right.dataset.productSlug || "";
        if (recommendedSlug) {
          if (leftSlug === recommendedSlug && rightSlug !== recommendedSlug) {
            return -1;
          }
          if (rightSlug === recommendedSlug && leftSlug !== recommendedSlug) {
            return 1;
          }
        }

        const leftPrice = Number(left.dataset.productPrice || 0);
        const rightPrice = Number(right.dataset.productPrice || 0);
        const leftRating = Number(left.dataset.productRating || 0);
        const rightRating = Number(right.dataset.productRating || 0);

        if (sortOption === "price_low") {
          return leftPrice - rightPrice || rightRating - leftRating;
        }
        if (sortOption === "price_high") {
          return rightPrice - leftPrice || rightRating - leftRating;
        }
        if (sortOption === "rating") {
          return rightRating - leftRating || leftPrice - rightPrice;
        }
        return rightRating - leftRating || leftPrice - rightPrice;
      });

      const fragment = document.createDocumentFragment();
      visibleCards.forEach(function (card) {
        fragment.appendChild(card);
      });
      grid.appendChild(fragment);
    }

    function updateCatalog() {
      const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
      const category = categoryInput ? categoryInput.value || "All" : "All";
      const useCase = useCaseInput ? useCaseInput.value || "" : "";
      const sortOption = sortSelect ? sortSelect.value || "featured" : "featured";
      const visibleCards = [];

      cards.forEach(function (card) {
        const rawSearch = (card.dataset.search || "").trim();
        const fallbackSearch = ((card.dataset.productName || "") + " " + (card.textContent || "")).trim();
        const searchText = (rawSearch ? rawSearch : fallbackSearch).toLowerCase();
        const categoryText = card.dataset.productCategory || "";
        const useCaseText = (card.textContent || "").toLowerCase();
        const matchesQuery = !query || searchText.indexOf(query) !== -1;
        const matchesCategory = category === "All" || categoryText === category;
        const matchesUseCase = !useCase || useCaseText.indexOf(useCase.toLowerCase()) !== -1;
        const isVisible = matchesQuery && matchesCategory && matchesUseCase;

        card.hidden = !isVisible;
        if (isVisible) {
          visibleCards.push(card);
        }
      });

      sortCards(visibleCards, sortOption);

      if (countLabel) {
        countLabel.textContent = visibleCards.length + " product" + (visibleCards.length === 1 ? "" : "s");
      }
      if (emptyState) {
        emptyState.hidden = visibleCards.length > 0;
      }

      updateUrl(query, category, sortOption, useCase);
      root.classList.add("is-ready");

      if (!recommendationFocused && recommendedSlug) {
        const recommendedCard = grid.querySelector('.store-card[data-product-slug="' + recommendedSlug + '"]');
        if (recommendedCard && !recommendedCard.hidden) {
          recommendationFocused = true;
          window.setTimeout(function () {
            recommendedCard.scrollIntoView({ behavior: "smooth", block: "center" });
          }, 280);
        }
      }
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        setActiveTab(tab.dataset.storeCategory || "All");
        updateCatalog();
      });
    });

    useCaseTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        const nextUseCase = useCaseInput && useCaseInput.value === (tab.dataset.storeUseCase || "") ? "" : (tab.dataset.storeUseCase || "");
        setActiveUseCase(nextUseCase);
        updateCatalog();
      });
    });

    searchInput && searchInput.addEventListener("input", updateCatalog);
    sortSelect && sortSelect.addEventListener("change", updateCatalog);

    if (filterForm) {
      filterForm.addEventListener("submit", function (event) {
        event.preventDefault();
        updateCatalog();
      });
    }

    setActiveTab(initialCategory);
    setActiveUseCase(initialUseCase);
    window.requestAnimationFrame(updateCatalog);
  }

  document.addEventListener("click", function (event) {
    const buyButton = event.target.closest("[data-buy-product]");
    if (!buyButton) {
      return;
    }

    event.preventDefault();
    if (buyButton.disabled) {
      return;
    }
    startCheckout(buyButton);
  });

  document.addEventListener("DOMContentLoaded", function () {
    bindStoreImages(document);
    initStoreCatalog();
  });
})();
