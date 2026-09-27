import { useEffect, type RefObject } from "react";

// 管理選單離開時關閉，Escape 返回原 trigger。
export function useDismissibleMenu(
  menu: RefObject<HTMLDetailsElement | null>,
  opener: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    const dismissOutside = (event: Event) => {
      const details = menu.current;
      if (details?.open && event.target instanceof Node && !details.contains(event.target))
        details.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !menu.current?.open) return;
      event.preventDefault();
      menu.current.open = false;
      opener.current?.focus({ preventScroll: true });
    };
    document.addEventListener("pointerdown", dismissOutside, true);
    document.addEventListener("focusin", dismissOutside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside, true);
      document.removeEventListener("focusin", dismissOutside);
      document.removeEventListener("keydown", escape);
    };
  }, [menu, opener]);
}
