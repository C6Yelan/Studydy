document.addEventListener('DOMContentLoaded', () => {
    
    // --- 1. 登出選單邏輯 ---
    const userProfileBtn = document.getElementById('user-profile-btn');
    const logoutMenu = document.getElementById('logout-menu');

    // 點擊頭像切換選單
    userProfileBtn.addEventListener('click', (e) => {
        e.stopPropagation(); // 防止事件冒泡
        logoutMenu.classList.toggle('show');
    });

    // 點擊頁面其他地方關閉選單
    document.addEventListener('click', (e) => {
        if (!userProfileBtn.contains(e.target) && !logoutMenu.contains(e.target)) {
            logoutMenu.classList.remove('show');
        }
    });

    // --- 2. 檔案上傳模擬 (維持不變) ---
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFiles(e.target.files);
        }
    });

    // Drag & Drop
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        handleFiles(files);
    });

    function handleFiles(files) {
        const file = files[0];
        // 模擬上傳 UI 變化
        const title = dropZone.querySelector('.dash-convert__title');
        const desc = dropZone.querySelector('.dash-convert__desc');
        const icon = dropZone.querySelector('.dash-convert__icon-wrapper i');

        title.innerText = `正在分析 ${file.name}...`;
        desc.innerText = 'AI 正在生成故事，請稍候...';
        icon.className = 'fas fa-spinner fa-spin';

        setTimeout(() => {
            alert('上傳成功！');
            // 還原或跳轉
            title.innerText = '分析完成';
            icon.className = 'fas fa-check';
        }, 1500);
    }
});