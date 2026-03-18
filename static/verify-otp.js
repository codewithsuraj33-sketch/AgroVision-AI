(function () {
  function initVerifyOtp() {
    var inputs = document.querySelectorAll(".otp-input");
    var form = document.getElementById("otp-form");
    var finalInput = document.getElementById("final-otp");
    var btn = document.getElementById("verify-btn");

    if (!inputs.length || !form || !finalInput) {
      return;
    }

    function updateFinalOTP() {
      var otp = "";
      inputs.forEach(function (input) {
        otp += input.value || "";
      });
      finalInput.value = otp;
    }

    inputs.forEach(function (input, index) {
      input.setAttribute("inputmode", "numeric");
      input.setAttribute("pattern", "[0-9]*");

      input.addEventListener("keyup", function (e) {
        if (e.key >= "0" && e.key <= "9") {
          if (index < inputs.length - 1) {
            inputs[index + 1].focus();
          }
        } else if (e.key === "Backspace") {
          if (index > 0) {
            inputs[index - 1].focus();
          }
        }
        updateFinalOTP();
      });

      input.addEventListener("paste", function (e) {
        var data = (e.clipboardData && e.clipboardData.getData("text")) || "";
        if (data.length === 6 && /^\d+$/.test(data)) {
          data.split("").forEach(function (char, i) {
            if (inputs[i]) {
              inputs[i].value = char;
            }
          });
          updateFinalOTP();
        }
      });
    });

    form.addEventListener("submit", function () {
      if (btn) {
        btn.classList.add("loading");
      }
      updateFinalOTP();
    });

    window.addEventListener("load", function () {
      if (inputs[0]) {
        inputs[0].focus();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVerifyOtp);
  } else {
    initVerifyOtp();
  }
})();

