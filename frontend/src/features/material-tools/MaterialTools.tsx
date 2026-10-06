import { useEffect, useRef, useState } from 'react';
import type { StudydyApiClient } from '../../api/client';
import { Icon } from '../../ui/Icon';
import { VoicePanel } from './VoicePanel';
import './styles.css';

export function MaterialTools({ api, materialId }: { api: StudydyApiClient; materialId: string }) {
 const [open, setOpen] = useState(false);
 const panel = useRef<HTMLElement>(null);
 const trigger = useRef<HTMLButtonElement>(null);
 const show = () => {
  for (const audio of document.querySelectorAll<HTMLMediaElement>('audio, video')) audio.pause();
  setOpen(true);
 };
 const close = () => { setOpen(false); trigger.current?.focus(); };
 useEffect(() => { if (open) panel.current?.querySelector<HTMLButtonElement>("button")?.focus(); }, [open]);
 return <div className="material-tools">
  <button ref={trigger} className="secondary-button" onClick={open ? close : show} aria-expanded={open} aria-controls="voice-conversation"><Icon name="microphone" size={17} />語音問答</button>
  {open && <section ref={panel} id="voice-conversation" className="material-tool-panel is-voice voice-bubble" role="dialog" aria-modal="false" aria-label="教材語音問答" onKeyDown={e => { if (e.key === 'Escape' && !(e.target as HTMLElement).closest('dialog')) { e.stopPropagation(); close(); } }}>
   <header><div className="tool-panel-title"><span className="tool-panel-icon"><Icon name="microphone" size={21} /></span><h2>教材語音問答</h2></div><button className="secondary-button" onClick={close}>關閉</button></header>
   <div className="tool-panel-body"><VoicePanel api={api} materialId={materialId} /></div>
  </section>}
 </div>;
}
