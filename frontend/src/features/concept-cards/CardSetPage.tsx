import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { Flashcard } from "./Flashcard";

export function CardSetPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId: string; materialId?: string }) {
  const [view, setView] = useState<CardSetView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.getCardSet(cardSetId).then(
      (result) => { if (!cancelled) { if (!materialId) { writeRoute({ name: "card-set", cardSetId, materialId: result.material_id }, true); return; } if (result.material_id !== materialId) setError("這個卡組不屬於此教材。"); else setView(result); } },
      (failure) => { if (!cancelled) setError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, cardSetId, materialId, reload]);
  const back = () => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" });
  if (error || !view) return <section className="cards-page"><button className="text-button" type="button" onClick={back}>← 返回卡組</button><StateView title={error ? "無法開啟卡組" : "正在讀取概念卡"} description={error ?? "正在載入已保存的重點。"} tone={error ? "failure" : "loading"} live={!error} action={error ? <button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button> : undefined} /></section>;
  return <CardStudy view={view} apiClient={apiClient} materialId={materialId} />;
}

function CardStudy({ view, apiClient, materialId }: { view: CardSetView; apiClient: StudydyApiClient; materialId?: string }) {
  const [order, setOrder] = useState(() => view.cards.map((_, index) => index));
  const [position, setPosition] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [finished, setFinished] = useState(false);
  const [notice, setNotice] = useState("");
  const stage = useRef<HTMLElement>(null);
  const move = (next: number) => {
    setNotice("");
    if (next >= order.length) { setFinished(true); return; }
    if (next < 0) return;
    setPosition(next);
    setFlipped(false);
  };
  useEffect(() => {
    stage.current?.focus({ preventScroll: true });
  }, [position, finished, flipped]);
  const restart = (shuffle: boolean) => {
    const next = view.cards.map((_, index) => index);
    if (shuffle) {
      for (let i = next.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [next[i], next[j]] = [next[j], next[i]];
      }
    }
    setOrder(next);
    setPosition(0);
    setFlipped(false);
    setFinished(false);
    setNotice(shuffle ? "已洗牌，從第一張開始。" : "已回到第一張。");
  };
  const keys = (event: KeyboardEvent) => {
    if (finished || event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]")) return;
    const target = event.target as HTMLElement;
    // 焦點在程式碼內時保留方向鍵的原生捲動，不切換卡片。
    if (target.closest("input, textarea, select, [contenteditable=true], details, .is-code")) return;
    if (event.key === "ArrowRight") { event.preventDefault(); move(position + 1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); move(position - 1); }
    else if (event.key === " " && !target.closest("button, a, summary")) { event.preventDefault(); setFlipped(!flipped); }
  };
  return <section className="cards-page cards-study" onKeyDown={keys}>
    <button className="cards-back text-button" type="button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" })}><Icon name="arrow-left" size={17} /> 返回卡組</button>
    <header className="cards-study-heading"><div><span className="cards-eyebrow">概念卡複習</span><h1>{view.name}</h1>{!materialId && <p><Icon name="book" size={15} /> {view.material_name}</p>}</div><div className="state-actions"><span className="deck-count">{view.card_count} 張卡片</span></div></header>
    {!view.is_current_revision && <p className="cards-notice">教材已有新版。此卡組保留建立時的教材內容與來源。</p>}
    <section ref={stage} tabIndex={-1} className="cards-study-stage" aria-label={finished ? "本輪瀏覽完成" : `第 ${position + 1} 張概念卡`}>
      {finished ? <div className="cards-complete"><span className="cards-empty-icon"><Icon name="check" size={36} /></span><h2>已瀏覽全部卡片</h2><p>這一輪看過了 {view.card_count} 張概念卡。想再回顧一次嗎？</p><div className="state-actions"><button className="primary-button" type="button" onClick={() => restart(false)}>再看一次</button><button className="secondary-button" type="button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" })}>返回卡組</button></div></div>
        : <><div className="cards-study-counter"><span aria-live="polite">第 <strong>{position + 1}</strong> / {order.length} 張</span><span>{flipped ? "重點面" : "概念面"}</span></div><div className="cards-position-track" aria-hidden="true"><span style={{ width: `${((position + 1) / order.length) * 100}%` }} /></div><Flashcard key={`${order[position]}:${flipped}`} card={view.cards[order[position]]} flipped={flipped} onFlip={() => setFlipped(!flipped)} apiClient={apiClient} sourceResolver={view.source_resolver} sourceQuality={view} />
          <div className="cards-study-controls"><button className="secondary-button" type="button" disabled={position === 0} onClick={() => move(position - 1)}><Icon name="arrow-left" size={18} /> 上一張</button><button className="primary-button" type="button" onClick={() => setFlipped(!flipped)}><Icon name="refresh" size={18} /> 翻面</button><button className="secondary-button" type="button" onClick={() => move(position + 1)}>{position === order.length - 1 ? "完成本輪" : "下一張"}<Icon name="chevron-right" size={18} /></button></div>
          <div className="cards-study-tools"><span className="cards-keyboard-hint">← → 切換卡片 · 空白鍵翻面</span><button className="text-button" type="button" disabled={order.length < 2} onClick={() => restart(true)}>洗牌重看</button></div></>}
      {notice && <p className="cards-study-notice" role="status">{notice}</p>}
    </section>
  </section>;
}
