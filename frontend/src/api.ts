// バックエンドの REST API。デモ店は cookie で訪問者ごとに分かれる。

export type Mark = { mark: "○" | "✕" | "△" | "？"; reasons: string[] };

export type Card = {
  item_id: string;
  name: string;
  name_ja: string;
  price: number;
  category: string;
  photo_url: string;
  photo_is_generated: boolean;
  is_alcohol: boolean;
  sold_out: boolean;
  remaining: number | null;
  description: string;
  how_to_eat: string;
  portion: string;
  allergens: string[];
  cross_contact: string[];
  taste_tags: string[];
  allergen_status: string;
  marks: Record<string, Mark>;
  reasons?: string[];
};

export type Guest = {
  id: string;
  label: string;
  allergies: string[];
  diets: string[];
  severe: boolean;
  declared: boolean;
  alcohol: { adult: boolean; driving: boolean } | null;
};

export type CartLine = { line_id: string; item_id: string; qty: number; for_guest: string | null };

export type Check = {
  decision: "ok" | "need_declaration" | "need_guest" | "need_alcohol_check" | "need_staff" | "rejected";
  reasons: string[];
  for_guest: string | null;
  verdicts: Record<string, { mark: string; verdict: string; reasons: string[] }>;
  warnings: string[];
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export const api = {
  demoStart: (reset = false) => call<{ store_id: string; created: boolean }>("POST", `/api/demo/start${reset ? "?reset=true" : ""}`),
  seat: (party_size: number, lang: string, table_no = "2") =>
    call<{ id: string; guests: Guest[] }>("POST", "/api/tables/seat", { party_size, lang, table_no }),
  view: (sid: string) => call<any>("GET", `/api/tables/${sid}`),
  declare: (sid: string, declarations: unknown[]) => call<any>("POST", `/api/tables/${sid}/declare`, { declarations }),
  alcohol: (sid: string, guest_id: string, adult: boolean, driving: boolean) =>
    call<any>("POST", `/api/tables/${sid}/alcohol`, { guest_id, adult, driving }),
  menu: (sid: string, category?: string) => call<Card[]>("GET", `/api/tables/${sid}/menu${category ? `?category=${category}` : ""}`),
  recommend: (sid: string) => call<Card[]>("GET", `/api/tables/${sid}/recommend`),
  addCart: (sid: string, item_id: string, qty: number, for_guest: string | null) =>
    call<{ check: Check; cart: CartLine[]; alternatives?: Card[] }>("POST", `/api/tables/${sid}/cart`, { item_id, qty, for_guest }),
  updateCart: (sid: string, line_id: string, qty: number) => call<any>("PATCH", `/api/tables/${sid}/cart/${line_id}`, { qty }),
  readBack: (sid: string) => call<any>("POST", `/api/tables/${sid}/read_back`),
  submit: (sid: string, confirm_token: string) =>
    call<any>("POST", `/api/tables/${sid}/submit`, { confirm_token, idempotency_key: confirm_token }),
  bill: (sid: string) => call<any>("POST", `/api/tables/${sid}/bill`),
  emergency: (sid: string) => call<any>("POST", `/api/tables/${sid}/emergency`),
  askStaff: (sid: string, item_id: string, for_guest: string | null) =>
    call<any>("POST", `/api/tables/${sid}/ask_staff`, { item_id, for_guest }),
  cancelLine: (oid: string, lid: string) => call<any>("POST", `/api/orders/${oid}/lines/${lid}/cancel`),
  ack: (nid: string) => call<any>("POST", `/api/notifications/${nid}/ack`),
  // 店員
  board: () => call<any>("GET", "/api/staff/board"),
  setLine: (oid: string, lid: string, status: string, reason = "") =>
    call<any>("POST", `/api/staff/orders/${oid}/lines/${lid}`, { status, reason }),
  setEscalation: (eid: string, status: string, handler = "店員") =>
    call<any>("POST", `/api/staff/escalations/${eid}`, { status, handler }),
  closeTable: (sid: string) => call<any>("POST", `/api/staff/tables/${sid}/close`),
  soldOut: (item_ids: string[], sold_out: boolean) => call<any>("POST", "/api/staff/sold_out", { item_ids, sold_out }),
  audit: () => call<any[]>("GET", "/api/staff/audit"),
};
