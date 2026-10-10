import type { ConceptCard } from "../../api/contracts";
import { claimText } from "../../ui/claim-text.ts";
import { hasCardEvidence } from "./card-relation.ts";
import catalog from "./icons/catalog.json" with { type: "json" };

export type CardObject = {
  kind: string; label: string; claimId: string; evidenceId: string;
  role: "concept" | "illustration" | "support"; score: number;
};
type Alias = { icon: string; cost: number; qualifier: string };
const aliases = new Map<string, Alias[]>();
for (const row of catalog.aliases) {
  const [term, icon, cost, , qualifier] = row as [string, string, number, string, string];
  const list = aliases.get(term) ?? [];
  list.push({ icon, cost, qualifier }); aliases.set(term, list);
}
const categories: Record<string, string> = catalog.icons;
const words = new Set(catalog.words.split(" "));
for (const word of aliases.keys()) words.add(word);
const boundary = /回應碼|確認號|序號|用戶端|客戶端|連接埠|表示|代表|包含|包括|可以|提供|操作|結果|控制|傳輸|處理|資料|服務|角色|原因|使用|呼叫|收到|不一定|不同/gu;
for (const word of boundary.source.split("|")) words.add(word);
const maxWord = Array.from(words).reduce((max, word) => Math.max(max, word.length), 0);
const generic = new Set("中心 說明 移動 空間 資料 接收 傳送 輸入 輸出 意義 描述 模型 媒介 方式 方法 規則 條件 形式 內容 部分 工作 功能 範圍 環境 時候 能力 組成 元素 意圖 種類 型態 狀態 程度 性質 特性".split(" "));
// 已知多義詞的語境規則；這是選圖限制，不是詞典提供的語意等價保證。
const senses: Record<string, [string[], string[]]> = {
  "command": [["keyboard", "mac", "shortcut", "快捷鍵", "鍵盤"], ["shell", "smtp", "協定", "命令列"]],
  "mouse": [["click", "computer", "cursor", "peripheral", "游標", "滑鼠", "輸入"], ["animal", "rodent", "seeds", "動物", "老鼠"]],
  "keyboard": [["computer", "hardware", "typing", "打字", "輸入", "電腦"], ["concert", "music", "musical", "piano", "鋼琴", "音樂"]],
  "apple": [["edible", "fruit", "水果", "食用"], ["brand", "company", "operating", "公司", "品牌"]],
  "brand-apple": [["brand", "company", "ios", "macos", "operating", "software", "公司", "品牌"], ["edible", "fruit", "水果"]],
  "cloud": [["atmosphere", "droplets", "weather", "大氣", "天氣", "水滴"], ["computing", "雲端運算"]],
  "cloud-computing": [["computing", "network", "server", "服務", "運算", "雲端"], ["weather", "大氣", "天氣"]],
  "virus": [["infection", "infects", "living", "organism", "感染", "生物"], ["computer", "malware", "software", "軟體", "電腦"]],
  "cell": [["biology", "living", "organism", "生物", "細胞"], ["battery", "spreadsheet", "儲存格", "試算表", "電池"]],
  "leaf": [["photosynthesis", "plant", "光合作用", "植物"], ["children", "graph", "node", "節點"]],
  "plant": [["garden", "pot", "potted", "盆栽", "花盆"], ["electricity", "factory", "工廠", "電廠"]],
  "scale": [["balance", "mass", "weigh", "天平", "稱重"], ["map", "musical", "ratio", "比例", "音階"]],
  "flask": [["chemistry", "lab", "laboratory", "化學", "實驗室", "燒瓶"], ["beaker", "thermos", "vacuum", "保溫", "燒杯"]],
  "server": [["hosting", "network", "rack", "role", "server", "主機", "伺服器", "角色"], ["restaurant", "waiter", "服務生", "餐廳"]],
  "bat": [["animal", "mammal", "nocturnal", "動物", "蝙蝠"], ["baseball", "cricket", "球棒"]],
  "window": [["windowpane", "windowsill", "glass", "house", "窗戶", "窗子", "玻璃"], ["congestion", "sequence", "tcp", "壅塞", "封包", "序號", "流量", "視窗"]],
  "heart": [["cardiac", "heartbeat", "blood", "love", "心臟", "心跳", "心血管", "愛情"], ["中心", "伺服器"]],
  "frame": [["photo frame", "picture frame", "畫框", "相框", "裱框"], ["packet", "network", "tcp", "ip", "封包", "連結層", "連結資訊"]],
  "book": [["library", "pages", "paper", "read", "書本", "書籍", "閱讀"], ["book a", "booking", "reserve", "預訂"]],
};
const needsContext = new Set(["mouse", "keyboard", "apple", "brand-apple", "cloud", "virus", "cell", "leaf", "plant", "scale", "flask", "bat", "window", "command", "heart", "frame"]);
const normalize = (text: string) => text.normalize("NFKC").toLowerCase().replace(/[-_]/g, " ").replace(/\s+/g, " ").trim();
function clauses(text: string) {
  // 字串、欄位識別符與編碼示例不是待畫的實物；保留周圍句子的語境。
  const visualText = text.normalize("NFKC")
    .replace(/`[^`]*`|"[^"\n]*"|'[^'\n]+'/g, match => " ".repeat(match.length))
    .replace(/\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b/g, match => aliases.has(normalize(match)) ? match : " ".repeat(match.length));
  return visualText.replace(/\bnot only\b/gi, "").split(/[。！？；，\n.!?;,]|\bbut\b/iu)
    .map(part => part.split(/\binstead of\b|\brather than\b|取代|代替|而非/iu)[0].trim())
    .filter(part => part && !/^(?:input|output|ascii|bits|groups|index|base64)\s*:/i.test(part) && !/不是|並非|并非|不(?:使用|用|包括|包含|代表|等於|等于|採用|采用)|沒有|没有|無需|无需|排除|避免|禁止|與其|与其|\b(?:not|no|never|without|cannot|isn't|aren't)\b/iu.test(part));
}
function mentions(text: string) {
  const tokens: { word: string; offset: number }[] = [];
  for (const match of text.matchAll(/[\u3400-\u9fff]+|[a-zA-Z0-9]+|[^\w\s]/gu)) {
    const block = match[0], offset = match.index;
    if (/^[\u3400-\u9fff]/u.test(block)) {
      const cuts = [...new Set([0, block.length, ...Array.from(block.matchAll(boundary), m => [m.index, m.index + m[0].length]).flat()])].sort((a,b) => a-b);
      for (let i = 0; i < block.length;) {
        const end = cuts.find(p => p > i)!;
        let size = Math.min(end-i, maxWord);
        while (size > 1 && !words.has(block.slice(i,i+size))) size--;
        tokens.push({ word: block.slice(i,i+size), offset: offset+i }); i += size;
      }
    } else tokens.push({ word: block.toLowerCase(), offset });
  }
  const found: { term: string; offset: number; options: Alias[] }[] = [];
  for (let i = 0; i < tokens.length;) {
    let size = Math.min(5, tokens.length-i), matched = false;
    for (; size > 0; size--) {
      const term = normalize(tokens.slice(i,i+size).map(t => t.word).join(" "));
      const options = aliases.get(term);
      if (options) { found.push({ term, offset: tokens[i].offset, options }); i += size; matched = true; break; }
    }
    if (!matched) i++;
  }
  return found;
}
function contains(text: string, term: string) {
  return /^[\x00-\x7F]+$/.test(term)
    ? new RegExp(`(?<![a-z])${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![a-z])`, "i").test(text)
    : text.includes(term);
}
function senseScore(icon: string, context: string): number | null {
  if (/(?:-off|-plus|-minus|-check|-x|-question|-\d+)$/.test(icon)) return null;
  const category = categories[icon];
  if ((category === "Brand" && icon !== "brand-apple") || ["Letters", "Numbers", "Mood", "Gender", "Gestures", "Zodiac"].includes(category)) return null;
  const ctx = context.toLowerCase(), sense = senses[icon];
  if (icon === "connection" && /無連線|无连接|connectionless|unconnected|without a connection/u.test(ctx)) return null;
  if (sense?.[1].some(w => contains(ctx,w))) return null;
  const boost = sense?.[0].some(w => contains(ctx,w)) ? 12 : 0;
  if (!boost && needsContext.has(icon)) return null;
  if (icon === "device-desktop" && /computer (?:virus|science|vision|operating)|電腦(?:病毒|科學)/u.test(ctx)) return null;
  if (icon === "file" && /file (?:a|the) (?:claim|case|complaint)/u.test(ctx)) return null;
  return boost;
}
const rank = (a: CardObject, b: CardObject) => b.score-a.score || (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0);

// 名稱或 Claim 必須在同一則 Claim 的有效來源中有對應物件；其他 Claim 不替它補證據。
// 圖示僅作概念／例子標記，不推導教材沒有的關係或流程。
export function selectCardObjects(card: ConceptCard): CardObject[] {
  const title = card.label.normalize("NFKC");
  const titleIds = new Set(mentions(title).flatMap(m => m.options.map(o => o.icon)));
  const ranked = new Map<string, CardObject>();
  card.claims.forEach((claim, ci) => {
    const evidence = claim.evidence.filter(hasCardEvidence);
    const witnesses = evidence.flatMap(e => clauses(e.quote).flatMap(quote => mentions(quote).map(m => ({ m, quote, evidenceId: e.evidence_id }))));
    const query = [...clauses(claimText(claim)).map(text => ({ text, fromTitle: false })), ...(evidence.length ? [{ text: title, fromTitle: true }] : [])];
    for (const { text, fromTitle } of query) for (const m of mentions(text)) {
      if (generic.has(m.term) || (m.term.length === 1 && /[^\x00-\x7F]/.test(m.term) && normalize(title) !== m.term)) continue;
      const local: CardObject[] = [];
      for (const option of m.options) for (const witness of witnesses) {
        if (!witness.m.options.some(o => o.icon === option.icon)) continue;
        const sense = senseScore(option.icon, `${text} ${witness.quote} ${title} ${option.qualifier}`);
        if (sense === null) continue;
        const main = fromTitle || titleIds.has(option.icon);
        const prefix = text.slice(0,m.offset).trim().toLowerCase();
        const subject = m.offset === 0 || ["a","an","the","一台","一個","這個","該","此","例如","比如","包括","包含"].includes(prefix) || /^(?:use|select|choose|採用|使用)\s*(?:a|an|the)?$/.test(prefix);
        const example = /例如|比如|包括|包含/u.test(prefix);
        local.push({ kind: option.icon, label: m.term, claimId: claim.claim_id, evidenceId: witness.evidenceId,
          role: main ? "concept" : subject || example ? "illustration" : "support",
          score: (main ? 120 : example ? 75 : subject ? 65 : 34) + sense - option.cost*9 - ci*.5 });
      }
      const best = local.sort(rank)[0];
      if (best && (!ranked.has(best.kind) || best.score > ranked.get(best.kind)!.score)) ranked.set(best.kind,best);
    }
  });
  return [...ranked.values()].sort(rank);
}
