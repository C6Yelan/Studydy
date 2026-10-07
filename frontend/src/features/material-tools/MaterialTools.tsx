import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { StudydyApiClient } from '../../api/client';
import { Icon } from '../../ui/Icon';
import { VoicePanel } from './VoicePanel';
import './styles.css';

export function MaterialTools({ api, materialId }: { api: StudydyApiClient; materialId: string }) {
 const [open, setOpen] = useState(false);
 const panel = useRef<HTMLElement>(null);
 const trigger = useRef<HTMLButtonElement>(null);
 const opened = useRef(false);
 const show = () => {
  for (const audio of document.querySelectorAll<HTMLMediaElement>('audio, video')) audio.pause();
  setOpen(true);
 };
 const close = () => setOpen(false);
 useEffect(() => {
  if (open) { opened.current = true; panel.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus(); }
  else if (opened.current) trigger.current?.focus();
 }, [open]);
 // 浮動視窗屬於 viewport，不繼承內容區的寬度、transform 或 overflow。
 return createPortal(<div className="material-tools">
  <button ref={trigger} className="secondary-button" hidden={open} onClick={show} aria-expanded={open} aria-controls="voice-conversation"><Icon name="microphone" size={17} />教材問答</button>
  {open && <section ref={panel} id="voice-conversation" className="material-tool-panel is-voice voice-bubble" role="dialog" aria-modal="false" aria-label="教材問答" onKeyDown={e => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('dialog')) { e.stopPropagation(); close(); } }}>
   <VoicePanel api={api} materialId={materialId} onClose={close}/>
  </section>}
 </div>,document.body);
}
