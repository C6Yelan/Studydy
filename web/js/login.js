import { apiFetch } from "./api.js";

document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.querySelector('.login-form');

  if (!loginForm) {
    console.error('找不到登入表單！');
    return;
  }

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;

    if (!email || !password) {
      alert('請輸入電子郵件與密碼！');
      return;
    }

    try {
      await apiFetch('/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        credentials: 'include', // ⭐ session cookie
        body: JSON.stringify({ email, password })
      });

      window.location.href = 'dashboard.html';

    } catch (error) {
      alert('登入失敗，請檢查您的電子郵件與密碼！');
      console.error(error);
    }
  });
});