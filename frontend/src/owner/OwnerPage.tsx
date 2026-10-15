// 店主の画面（日本語）：メニュー写真の取り込み → 名前・価格の確認 → 食材の確認（アレルゲンはコードが換算）→ 翻訳 → 公開。
import { useEffect, useState } from "react";

const ALLERGEN_JA: Record<string, string> = {
  shrimp: "えび", crab: "かに", walnut: "くるみ", wheat: "小麦", buckwheat: "そば", egg: "卵", milk: "乳", peanut: "落花生",
  cashew: "カシューナッツ", almond: "アーモンド", abalone: "あわび", squid: "いか", salmon_roe: "いくら", orange: "オレンジ",
  kiwi: "キウイ", beef: "牛肉", sesame: "ごま", salmon: "さけ", mackerel: "さば", soybean: "大豆", chicken: "鶏肉",
  banana: "バナナ", pistachio: "ピスタチオ", pork: "豚肉", macadamia: "マカダミアナッツ", peach: "もも", yam: "やまいも",
  apple: "りんご", gelatin: "ゼラチン",
};
const TYPES: Record<string, string> = { izakaya: "居酒屋", teishoku: "定食屋", ramen: "ラーメン店", cafe: "カフェ" };
const POLICY_LABEL: Record<string, string> = {
  sold_out_multi: "一言で 2 品以上が品切れになるとき",
  sold_out_single: "1 品だけの品切れ",
  todays_pick: "今日のおすすめの変更",
  cancel_before_cooking: "調理前のお客様の取り消し",
};
const VALUE_LABEL: Record<string, string> = { auto: "AI が自動で", confirm: "店員の確認後", staff: "店員に任せる" };
const CATEGORY_JA: Record<string, string> = {
  drink: "飲み物", snack: "一品", fried: "揚げ物", grill: "焼き物・炒め物", sashimi: "刺身", noodle: "麺",
  rice: "ご飯もの", dessert: "甘味", topping: "トッピング", set: "セット",
};

async function call(method: string, path: string, body?: unknown) {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function OwnerPage() {
  const [items, setItems] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [type, setType] = useState("");
  const [policy, setPolicy] = useState<Record<string, string>>({});

  async function load() {
    setItems(await call("GET", "/api/owner/menu"));
  }
  useEffect(() => {
    void load();
  }, []);

  async function upload(file: File) {
    setBusy("写真を読み取っています…");
    const b64 = await new Promise<string>((res) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(",")[1]);
      fr.readAsDataURL(file);
    });
    const r = await call("POST", "/api/owner/menu/import", { image_base64: b64, mime: file.type || "image/jpeg" });
    setBusy(`${r.items.length} 品を書き写しました（業態の推定：${TYPES[r.business_type_guess] ?? "不明"}）。名前と価格を確かめてください。`);
    await load();
  }

  useEffect(() => {
    void call("GET", "/api/owner/policy").then(setPolicy);
  }, []);

  const drafts = items.filter((i) => !i.published);
  const published = items.filter((i) => i.published);

  return (
    <div className="owner">
      <style>{
        `.owner-table-wrap { overflow-x: auto; margin-bottom: 1.5rem; }
        .owner-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        .owner-table th, .owner-table td { padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }
        .owner-table th { position: sticky; top: 0; background: var(--bg-soft, #f7f7f8); font-weight: 600; white-space: nowrap; text-align: left; }
        .owner-table th.num, .owner-table td.num { text-align: right; white-space: nowrap; }
        .owner-table .col-ingredients, .owner-table .col-allergens { font-size: 0.85rem; word-break: break-word; max-width: 200px; }
        .owner-table .detail-cell { background: var(--bg-soft, #fafafa); padding: 12px 16px; }
        .tag.confirmed { background: rgba(34, 197, 94, 0.15); color: #15803d; }
        .tag.unconfirmed { background: rgba(99, 102, 241, 0.15); color: var(--indigo, #4f46e5); }
        .owner-policy-details { margin-top: 1.5rem; border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; }
        .owner-policy-details summary { font-size: 1rem; font-weight: bold; cursor: pointer; }
        .owner-policy-details[open] summary { margin-bottom: 12px; }`
      }</style>
      <header>
        <h1>NOREN 店主</h1>
        <label className="upload primary">
          メニュー写真を取り込む
          <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
        </label>
        <span className="type-switch">
          業態：
          {Object.entries(TYPES).map(([k, v]) => (
            <button
              key={k}
              className={type === k ? "chip on" : "chip"}
              onClick={async () => {
                const r = await call("PATCH", "/api/owner/store", { business_type: k });
                setType(k);
                setBusy(`業態を ${v} にしました（支払い：${r.payment_timing === "prepay" ? "先払い" : "後払い"}、お通し：${r.otoshi_price ? r.otoshi_price + " 円" : "なし"}）`);
              }}
            >
              {v}
            </button>
          ))}
        </span>
      </header>
      {busy && <div className="banner">{busy}</div>}
      <p className="note small">AI はメニューの書き写し・質問・翻訳だけを行います。アレルゲンは、店主が答えた食材からシステムが換算します（AI が推測することはありません）。</p>

      {drafts.length > 0 && (
        <section>
          <h2>確認待ち（{drafts.length}）</h2>
          <div className="owner-table-wrap">
            <table className="owner-table">
              <thead>
                <tr>
                  <th>料理名</th>
                  <th className="num">価格</th>
                  <th>分類</th>
                  <th>食材（店主の回答）</th>
                  <th>アレルゲン（換算結果）</th>
                  <th>状態</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {drafts.map((i) => (
                  <DraftRow key={i.id} item={i} items={items} reload={load} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h2>メニュー（{published.length} 品）</h2>
        <div className="owner-table-wrap">
          <table className="owner-table">
            <thead>
              <tr>
                <th>料理名</th>
                <th>分類</th>
                <th className="num">価格</th>
                <th>食材</th>
                <th>アレルゲン</th>
                <th>混入の可能性</th>
                <th>アレルギー情報</th>
              </tr>
            </thead>
            <tbody>
              {published.map((i) => (
                <PublishedRow key={i.id} item={i} items={items} reload={load} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="owner-policy-details">
        <summary>AI に任せる範囲（サービスポリシー）</summary>
        <div className="policy">
          {Object.entries(POLICY_LABEL).map(([k, label]) => (
            <div key={k} className="policy-row">
              <span>{label}</span>
              {(k === "cancel_before_cooking" ? ["auto", "staff"] : ["auto", "confirm"]).map((v) => (
                <button
                  key={v}
                  className={policy[k] === v ? "chip on" : "chip"}
                  onClick={async () => setPolicy(await call("PATCH", "/api/owner/policy", { key: k, value: v }))}
                >
                  {VALUE_LABEL[v]}
                </button>
              ))}
            </div>
          ))}
          <p className="small">重いアレルギー・急病・苦情・誤配膳は常に店員へ。価格とアレルゲンは AI が変えることはできません。</p>
        </div>
      </details>
    </div>
  );
}

function DraftRow({ item, items, reload }: { item: any; items: any[]; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(item.name.ja);
  const [price, setPrice] = useState(item.price);
  const [q, setQ] = useState<any>(null);
  const [chosen, setChosen] = useState<string[]>(item.ingredients ?? []);
  const [cross, setCross] = useState<string[]>([]);
  const [free, setFree] = useState("");
  const [result, setResult] = useState<any>(null);
  const [tr, setTr] = useState<any>(null);

  async function ask() {
    setQ(await call("POST", `/api/owner/menu/${item.id}/questions`));
  }
  async function confirm() {
    const ingredients = [...chosen, ...free.split(/[、,\s]+/).filter(Boolean)];
    setResult(await call("POST", `/api/owner/menu/${item.id}/ingredients`, { ingredients, cross_contact_items: cross }));
    await reload();
  }
  async function publish() {
    await call("POST", `/api/owner/menu/${item.id}/publish`, { name_ja: name, price, published: true });
    await reload();
  }

  return (
    <>
      <tr>
        <td>
          <b>{item.name.ja}</b>
          {item.name.en && <div className="small">{item.name.en}</div>}
        </td>
        <td className="num">¥{item.price.toLocaleString()}</td>
        <td>{CATEGORY_JA[item.category] ?? item.category ?? "—"}</td>
        <td className="col-ingredients">{item.ingredients?.length ? item.ingredients.join("、") : "—"}</td>
        <td className="col-allergens">
          {item.allergens?.length ? item.allergens.map((a: string) => ALLERGEN_JA[a] ?? a).join("・") : "—"}
        </td>
        <td>
          <span className={`tag ${item.allergen_status === "confirmed" ? "confirmed" : "unconfirmed"}`}>
            {item.allergen_status === "confirmed" ? "確認済み" : "未確認"}
          </span>
        </td>
        <td>
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {!q ? (
              <button className="chip" onClick={() => { setOpen(true); void ask(); }}>AIに質問</button>
            ) : (
              <button className="chip" onClick={() => setOpen(true)}>食材に回答</button>
            )}
            <button
              className="chip"
              onClick={async () => {
                setOpen(true);
                setTr(await call("POST", `/api/owner/menu/${item.id}/translate`));
              }}
            >
              翻訳
            </button>
            <button className="chip primary" onClick={publish}>公開</button>
            <button className="chip" onClick={() => setOpen(!open)}>{open ? "閉じる" : "確認"}</button>
          </div>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="detail-cell">
            <div className="owner-detail">
              <section>
                <h4>1. 名前と価格（写真と見比べて直してください）</h4>
                <input value={name} onChange={(e) => setName(e.target.value)} />
                <input type="number" value={price} onChange={(e) => setPrice(Number(e.target.value))} />
                <button className="primary" onClick={publish}>この内容で公開</button>
              </section>
              <section>
                <h4>2. 使っている食材</h4>
                {!q && <button onClick={ask}>AI に質問を作ってもらう</button>}
                {q && (
                  <>
                    <ul>{q.questions.map((s: string) => <li key={s}>{s}</li>)}</ul>
                    <div className="chips">
                      {[...new Set([...q.choices, ...chosen])].map((c: string) => (
                        <button
                          key={c}
                          className={chosen.includes(c) ? "chip on" : "chip"}
                          onClick={() => setChosen((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]))}
                        >
                          {c}
                        </button>
                      ))}
                    </div>
                    <input placeholder="ほかの食材（読点で区切る。市販品は「市販のタレ」など）" value={free} onChange={(e) => setFree(e.target.value)} list={`ingredients-${item.id}`} />
                    <datalist id={`ingredients-${item.id}`}>{q.all_ingredients.map((x: string) => <option key={x} value={x} />)}</datalist>
                    <p className="small">同じ油・器具を使う料理（混入の可能性として扱います）：</p>
                    <div className="chips">
                      {items.filter((x) => x.id !== item.id && x.allergens?.length).map((x) => (
                        <button
                          key={x.id}
                          className={cross.includes(x.id) ? "chip on" : "chip"}
                          onClick={() => setCross((cs) => (cs.includes(x.id) ? cs.filter((y) => y !== x.id) : [...cs, x.id]))}
                        >
                          {x.name.ja}
                        </button>
                      ))}
                    </div>
                    <button className="primary" onClick={confirm}>この食材で確認する</button>
                  </>
                )}
                {result && (
                  <div className="conv">
                    <div>アレルゲン（換算）：{result.allergens.map((a: string) => ALLERGEN_JA[a]).join("・") || "なし"}</div>
                    {result.maybe_allergens.length > 0 && <div>含む可能性：{result.maybe_allergens.map((a: string) => ALLERGEN_JA[a]).join("・")}</div>}
                    {result.cross_contact.length > 0 && <div>混入の可能性：{result.cross_contact.map((a: string) => ALLERGEN_JA[a]).join("・")}</div>}
                    {result.unknown_ingredients.length > 0 && <div className="warn">確認できない食材：{result.unknown_ingredients.join("・")} → 「未確認」のままです。食事制限のあるお客様には自動ではお出ししません。</div>}
                  </div>
                )}
              </section>
              <section>
                <h4>3. 翻訳</h4>
                <button onClick={async () => setTr(await call("POST", `/api/owner/menu/${item.id}/translate`))}>翻訳する</button>
                {tr && (
                  <div className="conv">
                    <div>英語：{tr.name.en} ／ 简体：{tr.name["zh-Hans"]} ／ 繁體：{tr.name["zh-Hant"]} ／ 한국어：{tr.name.ko}</div>
                    <div>確認用（英語を日本語に戻したもの）：{tr.back_translation_ja}</div>
                  </div>
                )}
              </section>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function PublishedRow({ item, items, reload }: { item: any; items: any[]; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(item.name.ja);
  const [price, setPrice] = useState(item.price);
  const [q, setQ] = useState<any>(null);
  const [chosen, setChosen] = useState<string[]>(item.ingredients ?? []);
  const [cross, setCross] = useState<string[]>([]);
  const [free, setFree] = useState("");
  const [result, setResult] = useState<any>(null);
  const [tr, setTr] = useState<any>(null);

  async function ask() {
    setQ(await call("POST", `/api/owner/menu/${item.id}/questions`));
  }
  async function confirm() {
    const ingredients = [...chosen, ...free.split(/[、,\s]+/).filter(Boolean)];
    setResult(await call("POST", `/api/owner/menu/${item.id}/ingredients`, { ingredients, cross_contact_items: cross }));
    await reload();
  }

  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
        <td>
          <b>{item.name.ja}</b>
          <div className="small">{item.name.en || "—"}</div>
        </td>
        <td>{CATEGORY_JA[item.category] ?? item.category ?? "—"}</td>
        <td className="num">¥{item.price?.toLocaleString() ?? "—"}</td>
        <td className="col-ingredients">{item.ingredients?.length ? item.ingredients.join("、") : "—"}</td>
        <td className="col-allergens">
          {item.allergens?.length ? item.allergens.map((a: string) => ALLERGEN_JA[a] ?? a).join("・") : "—"}
        </td>
        <td className="col-allergens">
          {item.cross_contact?.length ? item.cross_contact.map((a: string) => ALLERGEN_JA[a] ?? a).join("・") : "—"}
        </td>
        <td>
          <span className={`tag ${item.allergen_status === "confirmed" ? "confirmed" : "unconfirmed"}`}>
            {item.allergen_status === "confirmed" ? "確認済み" : "未確認"}
          </span>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="detail-cell">
            <div className="owner-detail">
              <section>
                <h4>1. 名前と価格</h4>
                <input value={name} onChange={(e) => setName(e.target.value)} />
                <input type="number" value={price} onChange={(e) => setPrice(Number(e.target.value))} />
                <button className="primary" onClick={async () => { await call("POST", `/api/owner/menu/${item.id}/publish`, { name_ja: name, price, published: true }); await reload(); }}>内容を更新</button>
              </section>
              <section>
                <h4>2. 使っている食材</h4>
                {!q && <button onClick={ask}>AI に質問を作ってもらう</button>}
                {q && (
                  <>
                    <ul>{q.questions.map((s: string) => <li key={s}>{s}</li>)}</ul>
                    <div className="chips">
                      {[...new Set([...q.choices, ...chosen])].map((c: string) => (
                        <button
                          key={c}
                          className={chosen.includes(c) ? "chip on" : "chip"}
                          onClick={() => setChosen((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]))}
                        >
                          {c}
                        </button>
                      ))}
                    </div>
                    <input placeholder="ほかの食材（読点で区切る。市販品は「市販のタレ」など）" value={free} onChange={(e) => setFree(e.target.value)} list={`ingredients-${item.id}`} />
                    <datalist id={`ingredients-${item.id}`}>{q.all_ingredients.map((x: string) => <option key={x} value={x} />)}</datalist>
                    <p className="small">同じ油・器具を使う料理（混入の可能性として扱います）：</p>
                    <div className="chips">
                      {items.filter((x) => x.id !== item.id && x.allergens?.length).map((x) => (
                        <button
                          key={x.id}
                          className={cross.includes(x.id) ? "chip on" : "chip"}
                          onClick={() => setCross((cs) => (cs.includes(x.id) ? cs.filter((y) => y !== x.id) : [...cs, x.id]))}
                        >
                          {x.name.ja}
                        </button>
                      ))}
                    </div>
                    <button className="primary" onClick={confirm}>この食材で確認する</button>
                  </>
                )}
                {result && (
                  <div className="conv">
                    <div>アレルゲン（換算）：{result.allergens.map((a: string) => ALLERGEN_JA[a]).join("・") || "なし"}</div>
                    {result.maybe_allergens.length > 0 && <div>含む可能性：{result.maybe_allergens.map((a: string) => ALLERGEN_JA[a]).join("・")}</div>}
                    {result.cross_contact.length > 0 && <div>混入の可能性：{result.cross_contact.map((a: string) => ALLERGEN_JA[a]).join("・")}</div>}
                    {result.unknown_ingredients.length > 0 && <div className="warn">確認できない食材：{result.unknown_ingredients.join("・")} → 「未確認」のままです。食事制限のあるお客様には自動ではお出ししません。</div>}
                  </div>
                )}
              </section>
              <section>
                <h4>3. 翻訳</h4>
                <button onClick={async () => setTr(await call("POST", `/api/owner/menu/${item.id}/translate`))}>翻訳する</button>
                {tr && (
                  <div className="conv">
                    <div>英語：{tr.name.en} ／ 简体：{tr.name["zh-Hans"]} ／ 繁體：{tr.name["zh-Hant"]} ／ 한국어：{tr.name.ko}</div>
                    <div>確認用（英語を日本語に戻したもの）：{tr.back_translation_ja}</div>
                  </div>
                )}
              </section>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
