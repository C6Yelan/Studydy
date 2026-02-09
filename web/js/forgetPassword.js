import { apiFetch } from "./api.js";

document.addEventListener("DOMContentLoaded", () => {
  // --- 0. Grab elements ---
  const otpInputs = document.querySelectorAll(".otp-input");
  const sendBtn = document.getElementById("send-code-btn");
  const emailMsg = document.getElementById("email-msg");
  const resetForm = document.getElementById("reset-form");

  // --- 1. OTP input logic ---
  otpInputs.forEach((input, index) => {
    input.addEventListener("input", (e) => {
      e.target.value = e.target.value.replace(/[^0-9]/g, "");
      if (e.target.value && index < otpInputs.length - 1) {
        otpInputs[index + 1].focus();
      }
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !input.value && index > 0) {
        otpInputs[index - 1].focus();
      }
    });

    input.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData("text");
      const digits = text.replace(/[^0-9]/g, "").split("").slice(0, 6);

      digits.forEach((digit, i) => {
        if (otpInputs[i]) otpInputs[i].value = digit;
      });

      if (digits.length > 0) {
        const targetIndex = Math.min(digits.length, otpInputs.length - 1);
        otpInputs[targetIndex].focus();
      }
    });
  });

  // --- 2. Send code button logic ---
  if (sendBtn) {
    sendBtn.addEventListener("click", async () => {
      const emailVal = document.getElementById("email").value.trim();
      if (!emailVal) {
        alert("請先輸入電子郵件");
        return;
      }

      sendBtn.disabled = true;
      sendBtn.innerText = "發送中...";

      try {
        await apiFetch("/auth/password-reset/request-code", {
          method: "POST",
          body: JSON.stringify({ email: emailVal }),
        });

        if (emailMsg) emailMsg.style.display = "block";

        let count = 60;
        const timer = setInterval(() => {
          sendBtn.innerText = `${count}s`;
          count--;
          if (count < 0) {
            clearInterval(timer);
            sendBtn.disabled = false;
            sendBtn.innerText = "重發";
          }
        }, 1000);
      } catch (err) {
        console.error(err);
        alert("發送驗證碼失敗，請稍後再試");
        sendBtn.disabled = false;
        sendBtn.innerText = "重發";
      }
    });
  }

  // --- 3. Toggle password (exposed globally) ---
  window.togglePassword = function (inputId, icon) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const show = input.type === "password";
    input.type = show ? "text" : "password";
    icon.classList.toggle("fa-eye", !show);
    icon.classList.toggle("fa-eye-slash", show);
  };

  // --- 4. Reset form submit ---
  if (!resetForm) return;

  resetForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    const email = document.getElementById("email").value.trim();
    const p1 = document.getElementById("new-password").value;
    const p2 = document.getElementById("confirm-password").value;

    let otpCode = "";
    otpInputs.forEach((input) => (otpCode += input.value));

    if (!email) {
      alert("請先輸入電子郵件");
      return;
    }

    if (otpCode.length < 6) {
      alert("請完整輸入 6 位數驗證碼");
      return;
    }

    if (p1 !== p2) {
      document.getElementById("password-error").style.display = "block";
      return;
    } else {
      document.getElementById("password-error").style.display = "none";
    }

    const btn = this.querySelector(".auth-btn");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 重設中...';

    try {
      await apiFetch("/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify({ 
          email,
          code: otpCode,
          new_password: p1,
        }),
      });

      alert("重設成功！即將跳轉登入頁面。");
      window.location.href = "login.html";
    } catch (err) {
      console.error(err);
      alert("重設失敗，請確認驗證碼或密碼後再試");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  });
});