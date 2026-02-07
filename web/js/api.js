export const API_BASE = "http://127.0.0.1:8000";

export async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "include",
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}