/* global Razorpay */

(function () {
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

  function getInt(value, fallback) {
    const n = Number.parseInt(String(value || ""), 10);
    return Number.isFinite(n) ? n : fallback;
  }

  async function postJson(url, data) {
    const csrfToken = getCsrfToken();
    const res = await fetch(url, {
      method: "POST",
      headers: Object.assign(
        { "Content-Type": "application/json" },
        csrfToken ? { "X-CSRFToken": csrfToken } : {}
      ),
      body: JSON.stringify(data || {}),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok || !payload || payload.success === false) {
      const msg = (payload && (payload.error || payload.message)) || "Request failed.";
      throw new Error(msg);
    }
    return payload;
  }

  function pageData() {
    const body = document.body;
    return {
      name: body.getAttribute("data-user-name") || "",
      email: body.getAttribute("data-user-email") || "",
      phone: body.getAttribute("data-user-phone") || "",
      plan: (body.getAttribute("data-user-plan") || "free").toLowerCase(),
      walletBalance: getInt(body.getAttribute("data-wallet-balance") || "0", 0),
    };
  }

  async function verifyPayment(paymentId, razorpayResponse) {
    return postJson("/api/subscription/verify-payment", {
      payment_id: paymentId,
      razorpay_order_id: razorpayResponse && razorpayResponse.razorpay_order_id ? razorpayResponse.razorpay_order_id : "",
      razorpay_payment_id: razorpayResponse && razorpayResponse.razorpay_payment_id ? razorpayResponse.razorpay_payment_id : "",
      razorpay_signature: razorpayResponse && razorpayResponse.razorpay_signature ? razorpayResponse.razorpay_signature : "",
    });
  }

  async function startCheckout(plan, walletUseInr) {
    const createRes = await postJson("/api/subscription/create-order", {
      plan,
      wallet_use_inr: walletUseInr || 0,
    });

    if (createRes.checkout_mode === "wallet") {
      alert("Subscription activated using wallet.");
      window.location.href = "/dashboard";
      return;
    }

    if (createRes.checkout_mode === "demo") {
      await verifyPayment(createRes.payment_id, null);
      alert("Demo payment successful. Subscription activated.");
      window.location.href = "/dashboard";
      return;
    }

    if (createRes.checkout_mode !== "razorpay") {
      throw new Error("Unsupported checkout mode.");
    }

    const user = pageData();
    const options = {
      key: createRes.key,
      amount: createRes.amount_paise,
      currency: createRes.currency,
      name: createRes.name,
      description: createRes.description,
      order_id: createRes.order_id,
      prefill: {
        name: user.name,
        email: user.email,
        contact: user.phone,
      },
      theme: { color: "#7dff2e" },
      handler: async function (response) {
        try {
          await verifyPayment(createRes.payment_id, response);
          alert("Payment successful. Subscription activated.");
          window.location.href = "/dashboard";
        } catch (err) {
          alert(err && err.message ? err.message : "Payment verification failed.");
        }
      },
    };

    if (!window.Razorpay) {
      throw new Error("Razorpay script not loaded.");
    }
    const rzp = new Razorpay(options);
    rzp.open();
  }

  function initBuyButtons() {
    const walletInput = document.getElementById("walletUseInput");
    document.querySelectorAll("[data-buy-plan]").forEach((btn) => {
      btn.addEventListener("click", async function () {
        const plan = String(btn.getAttribute("data-buy-plan") || "pro").toLowerCase();
        const useWallet = btn.getAttribute("data-use-wallet") === "1";
        const walletUse = useWallet ? 0 : getInt(walletInput && walletInput.value, 0);

        btn.disabled = true;
        try {
          const finalWalletUse = useWallet ? pageData().walletBalance : walletUse;
          await startCheckout(plan, finalWalletUse);
        } catch (err) {
          alert(err && err.message ? err.message : "Checkout failed.");
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  function initWalletCheck() {
    const walletBtn = document.getElementById("walletCheckBtn");
    const walletInput = document.getElementById("walletUseInput");
    if (!walletBtn || !walletInput) return;

    walletBtn.addEventListener("click", async function () {
      const value = getInt(walletInput.value, 0);
      const plan = "pro";
      walletBtn.disabled = true;
      try {
        const res = await postJson("/api/apply-wallet", { plan, wallet_use_inr: value });
        alert("Wallet applied: Rs " + res.wallet_use_inr + ". Amount due: Rs " + res.amount_due_inr + ".");
      } catch (err) {
        alert(err && err.message ? err.message : "Wallet check failed.");
      } finally {
        walletBtn.disabled = false;
      }
    });
  }

  initBuyButtons();
  initWalletCheck();
})();

