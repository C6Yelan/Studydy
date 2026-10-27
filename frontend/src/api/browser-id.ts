const key = "studydy.browser-id";

export function browserId(): string {
  const saved = localStorage.getItem(key);
  if (saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved)) return saved;
  const id = crypto.randomUUID();
  localStorage.setItem(key, id);
  return id;
}
