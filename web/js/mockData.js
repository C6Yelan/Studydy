// js/mockData.js

const MOCK_DB = {
    stories: {
        "default": {
            title: "行銷：4P 行銷組合",
            subtitle: "從 Chapter 1.pdf 轉換 <span class='difficulty-badge'>難度：中級</span>",
            
            // ★ 修改點：將內容改成「陣列」，每一項代表一頁
            pages: [
                {
                    contentTitle: "行銷：4P 行銷組合",
                    content: `
                    <p>在一間熱鬧的創新實驗室裡，一個全新的創作品 <strong>Spark (斯帕克)</strong> 睜開了眼睛。Spark 不是人，而是一項剛誕生、充滿潛力的產品，準備踏入市場。</p>
                    <p>「我好期待讓大家認識我！」Spark 興奮地說。</p>

                    <p>但行銷團隊笑著搖頭：「別急，Spark。在你見到顧客之前，你必須通過四道重要的關卡：
                    <span class="highlight" data-key="Product">產品 (Product)</span>、
                    <span class="highlight" data-key="Price">價格 (Price)</span>、
                    <span class="highlight" data-key="Place">通路 (Place)</span>、
                    <span class="highlight" data-key="Promotion">推廣 (Promotion)</span>。」</p>

                    <p><strong>產品 (Product)</strong><br>
                    公司提供的商品或服務，包含特色、品質、設計與品牌定位。</p>

                    <p><strong>價格 (Price)</strong><br>
                    顧客為了獲得產品所支付的代價，包含金錢與心理成本。</p>

                    <p><strong>通路 (Place)</strong><br>
                    讓顧客能在正確時間、正確地點買到產品。</p>

                    <p><strong>推廣 (Promotion)</strong><br>
                    與消費者溝通，讓他們知道「為什麼需要這個產品」。</p>

                    <p>Spark 完整經歷了 4P 的洗禮，正式成為一個成功上市的商品。</p>
                    `,
                    keywords: {
                    "產品 (Product)": "公司提供的商品或服務。",
                    "價格 (Price)": "顧客支付的金錢與心理成本。",
                    "通路 (Place)": "產品到消費者手中的路徑。",
                    "推廣 (Promotion)": "企業與消費者溝通的方式。",
                    "4P 理論": "行銷組合的基礎模型。"
                    }
                }
            ]

        },
        // 保留歷史結構
        "history": {
            title: "歷史：工業革命",
            subtitle: "Chapter 2",
            pages: [
                { contentTitle: "蒸汽的怒吼", content: "<p>18世紀的英國...</p>", keywords: {} }
            ]
        }
    },
    quizzes: [
        { id: 1, question: "以下哪一項 <span class='highlight-red'>不是</span> 4P 行銷組合的一部分？", options: {A:"推廣", B:"通路", C:"產品", D:"政策"}, correctAnswer: "D", explanation: "政策 (Policy) 不屬於 4P。" }
    ],
    initialFiles: []
};