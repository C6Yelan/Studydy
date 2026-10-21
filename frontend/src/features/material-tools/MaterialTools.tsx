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
  // 抽屜從分頁列下緣展開，聊天時仍可切換分頁；開啟後固定位置，不隨捲動移動。
  const toolbar = trigger.current?.closest('.material-learning-toolbar')?.getBoundingClientRect();
  setTop(toolbar ? Math.min(Math.max(toolbar.bottom + 8, 8), window.innerHeight / 2) : null);
  setOpen(true);
 };
 const close = () => setOpen(false);
 useEffect(() => {
  if (open) { opened.current = true; panel.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus(); }
  else if (opened.current) trigger.current?.focus();
 }, [open]);
 // 入口在教材分頁列右側；抽屜屬於 viewport，不繼承內容區的寬度、transform 或 overflow。
 return <>
  <button ref={trigger} className="material-qa-trigger" type="button" hidden={open} onClick={show} aria-expanded={open} aria-controls="voice-conversation" aria-label="教材問答"><Icon name="chat" size={18} /><span>教材問答</span></button>
  {open && createPortal(<section ref={panel} id="voice-conversation" className="material-tool-panel is-voice voice-bubble material-qa-drawer" style={top === null ? undefined : { top }} role="dialog" aria-modal="false" aria-label="教材問答" onKeyDown={e => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('dialog')) { e.stopPropagation(); close(); } }}>
   <VoicePanel api={api} materialId={materialId} revision={revision} onClose={close}/>
  </section>, document.body)}
 </>;
}
