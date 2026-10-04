import type { StudydyApiClient } from '../../api/client';
import type { MaterialLibraryItem } from '../../api/contracts';

// 只重用已通過 API 驗證的教材索引，頁面仍會重新讀取伺服器。
// API client 隨登入／帳號更換重建，索引不跨帳號，也不寫入瀏覽器持久儲存。
const materials = new WeakMap<StudydyApiClient, Map<string, MaterialLibraryItem>>();

export function rememberedMaterial(api: StudydyApiClient, materialId: string): MaterialLibraryItem | null {
  return materials.get(api)?.get(materialId) ?? null;
}

export function rememberMaterial(api: StudydyApiClient, material: MaterialLibraryItem): void {
  let index = materials.get(api);
  if (!index) { index = new Map(); materials.set(api, index); }
  index.set(material.material_id, material);
}
