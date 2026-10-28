import { useEffect, useRef, useState } from "react";
import { InlineConfirm } from "../../ui/InlineConfirm";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import { InlineNameEditor } from "./InlineNameEditor";
import { useDismissibleMenu } from "./useDismissibleMenu";
import { writeRoute } from "../../app/routes";
import type { MaterialLibraryItem, MaterialDiscardView } from "../../api/contracts";
import { Icon } from "../../ui/Icon";

// 按下滑鼠／觸控時是否有開著的管理選單；選單在 document 捕獲階段就會關閉，所以在 window 捕獲階段先記錄。
let menuOpenAtPointerDown = false;
if (typeof window !== "undefined")
  window.addEventListener("pointerdown", () => {
    menuOpenAtPointerDown = !!document.querySelector(".material-management-menu[open]");
  }, true);
export const pointerStartedWithMenuOpen = () => menuOpenAtPointerDown;

export function MaterialManagement({
  item,
  apiClient,
  deleting,
  onRenamed,
  onDeleted,
  onOpen,
}: {
  /** 點標題開啟教材（滑鼠捷徑；鍵盤使用卡片內的按鈕）。 */
  onOpen?: () => void;
  item: MaterialLibraryItem;
  apiClient: StudydyApiClient;
  deleting: boolean;
  onRenamed: (item: MaterialLibraryItem) => void;
  onDeleted: (state: MaterialDiscardView["state"]) => void;
}) {
  const [mode, setMode] = useState<"rename" | "delete" | null>(null);
  const [name, setName] = useState(item.display_name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menu = useRef<HTMLDetailsElement>(null);
  const opener = useRef<HTMLElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const interacted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useDismissibleMenu(menu, opener);
  useEffect(() => {
    if (!interacted.current || deleting) return;
    if (mode === "rename") {
      input.current?.focus();
      input.current?.select();
    } else if (mode !== "delete") opener.current?.focus({ preventScroll: true });
  }, [mode, deleting]);
  useEffect(() => {
    if (error && !busy && mode === "rename") input.current?.focus();
  }, [error, busy, mode]);
  const choose = (next: "rename" | "delete") => {
    if (menu.current) menu.current.open = false;
    interacted.current = true;
    setError(null);
    setName(item.display_name);
    setMode(next);
  };
  const close = () => {
    if (!inFlight.current) {
      setError(null);
      setMode(null);
    }
  };
  const valid =
    name.trim().length > 0 &&
    Array.from(name.trim()).length <= 40 &&
    !/[\p{Cc}\p{Cs}]/u.test(name);
  const submit = async () => {
    if (inFlight.current || !mode || (mode === "rename" && !valid)) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      if (mode === "rename")
        onRenamed(await apiClient.renameMaterial(item.material_id, name.trim()));
      else onDeleted((await apiClient.discardMaterial(item.material_id)).state);
      if (mounted.current) setMode(null);
    } catch (failure) {
      if (mounted.current)
        setError(
          `${mode === "rename" ? "無法重新命名教材。" : "無法刪除教材。"}${errorMessage(failure)}`,
        );
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <>
      {!deleting && (
        <details ref={menu} className="material-management-menu" hidden={mode !== null}>
          <summary
            ref={opener}
            role="button"
            tabIndex={0}
            aria-label={`管理「${item.display_name}」`}
          >
            ⋯
          </summary>
          <div>
            <button
              type="button"
              onClick={() => {
                if (menu.current) menu.current.open = false;
                writeRoute({ name: "material-sources", materialId: item.material_id });
              }}
            >
              <Icon name="file" size={16} />
              來源與更新
            </button>
            <button type="button" onClick={() => choose("rename")}>
              <Icon name="edit" size={16} />
              重新命名
            </button>
            <button
              className="material-delete-action"
              type="button"
              onClick={() => choose("delete")}
            >
              <Icon name="trash" size={16} />
              刪除教材
            </button>
          </div>
        </details>
      )}
      {mode !== "rename" && <h2 title={item.display_name} className={onOpen && !deleting ? "is-clickable" : undefined}
        // 點標題若只是為了關閉開著的選單，就不開啟教材。
        onClick={deleting || !onOpen ? undefined : () => { if (!menuOpenAtPointerDown) onOpen(); }}>{item.display_name}</h2>}
      {deleting ? (
        <p role="status" className="material-deleting">
          正在刪除…
        </p>
      ) : (
        mode === "rename" ? <InlineNameEditor maxLength={40} label="教材名稱" name={name} busy={busy} error={error} inputRef={input} onChange={setName} onSave={() => void submit()} onCancel={close} /> : mode === "delete" && <InlineConfirm message="確定刪除教材與相關資料？" label="刪除教材確認" confirmLabel="刪除"
          confirmAriaLabel="確認刪除" busyLabel="正在刪除…" busy={busy} error={error} onCancel={close} onConfirm={() => void submit()} />
      )}
    </>
  );
}
