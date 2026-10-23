import { useEffect, useRef, type ReactNode } from "react";

// 升級與取得徽章共用的慶祝對話框：開啟時背景不可操作、焦點移到對話框內的按鈕；
// Esc、點背景或按鈕關閉，關閉後焦點回到開啟前的位置。強制回應的對話框不自動關閉（WCAG 2.2.1），由使用者決定何時離開。
export function CelebrationDialog({ label, className, onClose, children }: {
  label: string; className: string; onClose: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => {
      dialog.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return <dialog ref={ref} className={className} role="dialog" aria-modal="true" aria-label={label}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {children}
  </dialog>;
}
