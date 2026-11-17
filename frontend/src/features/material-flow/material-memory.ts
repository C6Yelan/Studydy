import { useSyncExternalStore } from 'react';
import type { StudydyApiClient } from '../../api/client';
import type { MaterialLibraryItem } from '../../api/contracts';

// 只重用已通過 API 驗證的教材索引，頁面仍會重新讀取伺服器。
// API client 隨登入／帳號更換重建，索引不跨帳號，也不寫入瀏覽器持久儲存。
const materials = new WeakMap<StudydyApiClient, Map<string, MaterialLibraryItem>>();
// 教材側欄與更新提示掛在外框，頁面讀到新的教材索引時要跟著更新。
const listeners = new Set<() => void>();

export function rememberedMaterial(api: StudydyApiClient, materialId: string): MaterialLibraryItem | null {
  return materials.get(api)?.get(materialId) ?? null;
}

export function rememberMaterial(api: StudydyApiClient, material: MaterialLibraryItem): void {
  let index = materials.get(api);
  if (!index) { index = new Map(); materials.set(api, index); }
  index.set(material.material_id, material);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useRememberedMaterial(api: StudydyApiClient, materialId: string): MaterialLibraryItem | null {
  return useSyncExternalStore(subscribe, () => rememberedMaterial(api, materialId));
}
