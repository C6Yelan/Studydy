import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { StudydyApiClient } from '../../api/client';
import { Icon } from '../../ui/Icon';
import { VoicePanel } from './VoicePanel';
import './styles.css';

export function MaterialTools({ api, materialId, revision }: { api: StudydyApiClient; materialId: string; revision?: string }) {
 const [open, setOpen] = useState(false);
 const [top, setTop] = useState<number | null>(null);
 const panel = useRef<HTMLElement>(null);
 const trigger = useRef<HTMLButtonElement>(null);
 const opened = useRef(false);
 const show = () => {
  for (const audio of document.querySelectorAll<HTMLMediaElement>('audio, video')) audio.pause();
  // 桌機是教材頁首下緣展開的右側抽屜，聊天時仍可切換分頁；手機是全螢幕（由 CSS 定位）。
  // 開啟後固定位置，不隨捲動移動。
  const toolbar = trigger.current?.closest('.material-learning-toolbar')?.getBoundingClientRect();
  const phone = window.matchMedia('(max-width: 720px)').matches;
  setTop(toolbar && !phone ? Math.min(Math.max(toolbar.bottom + 8, 8), window.innerHeight / 2) : null);
  setOpen(true);
 };
 const close = () => setOpen(false);
 useEffect(() => {
  if (open) { opened.current = true; panel.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus(); }
  else if (opened.current) trigger.current?.focus();
  // 寬螢幕時問答固定在右側，主內容讓出空間，不蓋住地圖或教材。
  document.documentElement.classList.toggle('qa-panel-open', open);
  return () => document.documentElement.classList.remove('qa-panel-open');
 }, [open]);
 // 入口在教材分頁列右側，開啟後仍可再按一次收起；面板屬於 viewport，不繼承內容區的寬度、transform 或 overflow。
 return <>
  <button ref={trigger} className="material-qa-trigger" type="button" onClick={open ? close : show} aria-expanded={open} aria-controls="voice-conversation" aria-label="教材問答"><Icon name="chat" size={18} /><span>教材問答</span></button>
  {open && createPortal(<section ref={panel} id="voice-conversation" className="material-qa-drawer" style={top === null ? undefined : { top }} role="dialog" aria-modal="false" aria-label="教材問答" onKeyDown={e => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('dialog')) { e.stopPropagation(); close(); } }}>
   <VoicePanel api={api} materialId={materialId} revision={revision} onClose={close}/>
  </section>, document.body)}
 </>;
}
