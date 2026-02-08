import { apiFetch } from "./api.js";

document.addEventListener('DOMContentLoaded', () => {
  const registerForm = document.querySelector('.register-form');

  if (registerForm) {
    registerForm.addEventListener('submit',async (e) => {
      e.preventDefault();

      const password = document.getElementById('register-password').value;
      const confirmPassword = document.getElementById('register-confirm-password').value;
      const captchaInput = document.getElementById('register-captcha').value.trim();

      const correctCaptcha = "8FhK3";

      if (password !== confirmPassword) {
        alert('❌ 錯誤：兩次輸入的密碼不一致，請重新確認！');
        return;
      }

      if (captchaInput.toLowerCase() !== correctCaptcha.toLowerCase()) {
        alert('❌ 錯誤：驗證碼不正確，請重新輸入！(提示: 8FhK3)');
        return;
      }

      try{
        await apiFetch('/auth/register', {
          method : 'POST',
          body : JSON.stringify({
            email,password}),
          });
          window.location.href = 'login.html';
        }catch(error){
          alert('註冊失敗，請檢查您的輸入！');
          console.error(error);
        }
    });
  }
});