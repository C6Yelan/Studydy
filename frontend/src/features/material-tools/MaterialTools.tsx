import { useEffect, useRef, useState } from 'react';
import type { StudydyApiClient } from '../../api/client';
import { Icon } from '../../ui/Icon';
import { VoicePanel } from './VoicePanel';
import './styles.css';

export function MaterialTools({ api, materialId }: { api: StudydyApiClient; materialId: string }) {
 const [open, setOpen] = useState(false);
 const dialog = useRef<HTMLDialogElement>(null);
 const show = () => {
  for (const audio of document.querySelectorAll<HTMLAudioElement>('audio')) audio.pause();
  setOpen(true);
 };
 const close = () => { dialog.current?.close(); setOpen(false); };
 useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
 return <div className="material-tools">
  <button className="secondary-button" onClick={show} aria-haspopup="dialog"><Icon name="microphone" size={17} />語音問答</button>
  {open && <dialog ref={dialog} className="material-tool-panel is-voice" aria-label="教材語音問答" onCancel={e => { if (e.target === e.currentTarget) { e.preventDefault(); close(); } }} onClose={e => { if (e.target === e.currentTarget) setOpen(false); }}>
   <header><div className="tool-panel-title"><span className="tool-panel-icon"><Icon name="microphone" size={21} /></span><h2>教材語音問答</h2></div><button className="secondary-button" onClick={close}>關閉</button></header>
   <div className="tool-panel-body"><VoicePanel api={api} materialId={materialId} /></div>
  </dialog>}
 </div>;
}
