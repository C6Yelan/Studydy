import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { StudydyApiClient } from '../../api/client';
import { Icon } from '../../ui/Icon';
import { VoicePanel } from './VoicePanel';
import './styles.css';

export function MaterialTools({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision?: string }) {
 const [open, setOpen] = useState(false);
 const [prefill, setPrefill] = useState<{ text: string; id: string }>();
 const panel = useRef<HTMLElement>(null);
 const trigger = useRef<HTMLButtonElement>(null);
 const opened = useRef(false);
 const show = () => {
  for (const audio of document.querySelectorAll<HTMLMediaElement>('audio, video')) audio.pause();
  // 桌機是右側抽屜，聊天時仍可切換教材側欄的項目；手機是全螢幕（由 CSS 定位）。
  setOpen(true);
 };
 const close = () => setOpen(false);
 useEffect(() => {
  const fill = (event: Event) => {
   const detail = (event as CustomEvent<{ materialId: string; text: string }>).detail;
   if (detail.materialId !== materialId) return;
   setPrefill({ text: detail.text, id: crypto.randomUUID() });
   show();
  };
  window.addEventListener('studydy:ask-note', fill);
  return () => window.removeEventListener('studydy:ask-note', fill);
 }, [materialId]);
 useEffect(() => {
  if (open) { opened.current = true; panel.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus(); }
  else if (opened.current) trigger.current?.focus();
  // 寬螢幕時問答固定在右側，主內容讓出空間，不蓋住地圖或教材。
  document.documentElement.classList.toggle('qa-panel-open', open);
  return () => document.documentElement.classList.remove('qa-panel-open');
 }, [open]);
 // 入口是教材側欄的「問小幫手」，開啟後仍可再按一次收起；面板屬於 viewport，不繼承內容區的寬度、transform 或 overflow。
 return <>
  <button ref={trigger} className="rail-link material-qa-trigger" type="button" onClick={open ? close : show} aria-expanded={open} aria-controls="voice-conversation"><i className="is-blue" aria-hidden="true"><Icon name="chat" size={20} /></i><span className="rail-label">問小幫手</span></button>
  {open && createPortal(<section ref={panel} id="voice-conversation" className="material-qa-drawer" role="dialog" aria-modal="false" aria-label="Studydy AI BOT" onKeyDown={e => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('dialog')) { e.stopPropagation(); close(); } }}>
   <VoicePanel api={api} materialId={materialId} revision={revision} prefill={prefill} onClose={close}/>
  </section>, document.body)}
 </>;
}
