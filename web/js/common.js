// js/common.js

// 專案全域設定與工具
const AppConfig = {
    DB_KEY_FILES: 'studydy_files',
    DB_KEY_USER: 'studydy_user',
    DB_KEY_QUIZ: 'quizResult' // 維持您原本的 key 名稱
};

// 初始化資料庫 (若 LocalStorage 為空，則寫入預設 Mock Data)
function initDatabase() {
    const existingFiles = localStorage.getItem(AppConfig.DB_KEY_FILES);
    
    if (!existingFiles && typeof MOCK_DB !== 'undefined') {
        console.log("🔥 初始化 Studydy 模擬資料庫...");
        localStorage.setItem(AppConfig.DB_KEY_FILES, JSON.stringify(MOCK_DB.initialFiles));
    }
}

// 模擬 API 延遲 (Loading 效果)
function simulateApiCall(callback, duration = 800) {
    return new Promise((resolve) => {
        setTimeout(() => {
            if (callback) callback();
            resolve();
        }, duration);
    });
}

// 頁面載入時自動執行初始化
document.addEventListener('DOMContentLoaded', () => {
    initDatabase();
});