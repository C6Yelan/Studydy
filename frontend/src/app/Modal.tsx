import { useEffect, useRef, type ReactNode } from "react";

export function Modal({ label, open, onClose, className, children, onKeyDown }: {
  label: string; open: boolean; onClose: () => void; className: string; children: ReactNode; onKeyDown?: React.KeyboardEventHandler<HTMLDialogElement>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`header-dialog ${className}`}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onKeyDown={onKeyDown}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      {open && children}
    </dialog>
  );
}
