// お客様の画面：左に会話、右に商品ウィンドウ。会話・文字・タッチは同じカートを操作する。
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Card, CartLine, Check, Guest } from "../api";
import { Mic, Player } from "../audio";
import { allergenName, DIETS, expertLabel, hasVoice, Lang, statusLabel, t } from "../i18n";

type Turn = { role: "user" | "ai"; text: string; final: boolean };
const CARD_KINDS = new Set(["sold_out", "rejected", "last_order"]); // お客様の画面にカードで知らせる通知
type VoiceState = "closed" | "connecting" | "listening" | "speaking" | "unavailable";
type Sheet =
  | { kind: "guest"; item_id: string; qty: number; verdicts?: Check["verdicts"]; multi?: boolean }
  | { kind: "alcohol"; item_id: string; qty: number; for_guest: string }
  | { kind: "blocked"; item_id: string; check: Check; alternatives: Card[] }
  | { kind: "readback"; data: any }
  | { kind: "bill"; data: any }
  | { kind: "declaration"; allergies: string[]; diets: string[] }
  | { kind: "options"; item_id: string; qty: number }
  | null;

export function TableScreen({ sid, lang }: { sid: string; lang: Lang }) {
  const [voice, setVoice] = useState<VoiceState>("closed");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [tab, setTab] = useState<"recommend" | "menu" | "order">("recommend");
  const [recs, setRecs] = useState<Card[]>([]);
  const [menu, setMenu] = useState<Card[]>([]);
  const [spot, setSpot] = useState<Card[]>([]);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [guests, setGuests] = useState<Guest[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [experts, setExperts] = useState<string[]>([]);
  const [facts, setFacts] = useState<string[]>([]);
  const [notes, setNotes] = useState<any[]>([]);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [text, setText] = useState("");
  const [banner, setBanner] = useState("");
  const [offline, setOffline] = useState(false);
  const [staffComing, setStaffComing] = useState(false);
  const [mode, setMode] = useState<"reception" | "interpret">("reception");
  const [bigText, setBigText] = useState<boolean>(() => {
    try {
      return localStorage.getItem("noren_big_text") === "true";
    } catch {
      return false;
    }
  });

  function toggleBigText() {
    setBigText((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("noren_big_text", String(next));
      } catch {}
      return next;
    });
  }
  const ws = useRef<WebSocket | null>(null);
  const mic = useRef(new Mic());
  const player = useRef(new Player());
  const voiceRef = useRef<VoiceState>("closed");
  const logRef = useRef<HTMLDivElement>(null);

  const cards = useMemo(() => Object.fromEntries([...menu, ...recs, ...spot].map((c) => [c.item_id, c])), [menu, recs, spot]);

  async function refresh() {
    const v = await api.view(sid);
    setCart(v.session.cart);
    setGuests(v.session.guests);
    setOrders(v.orders);
  }

  useEffect(() => {
    void refresh();
    void api.menu(sid).then(setMenu);
    void api.recommend(sid).then(setRecs);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let alive = true;
    let retry = 0;
    let timer: number | undefined;
    // 切れたらつなぎ直す（1, 2, 4… 最大 10 秒）。カートと注文はサーバーから取り直し、切断中の通知はサーバーが送り直す
    const connect = () => {
      const sock = new WebSocket(`${proto}://${location.host}/ws/tables/${sid}`);
      sock.binaryType = "arraybuffer";
      sock.onmessage = (e) => (typeof e.data === "string" ? onMessage(JSON.parse(e.data)) : player.current.play(e.data));
      sock.onopen = () => {
        if (retry > 0) {
          setOffline(false);
          void refresh();
        }
        retry = 0;
      };
      sock.onclose = () => {
        if (!alive) return;
        mic.current.stop();
        player.current.stop();
        if (voiceRef.current !== "closed") setVoiceState("closed");
        setOffline(true);
        timer = window.setTimeout(connect, Math.min(1000 * 2 ** retry++, 10000));
      };
      ws.current = sock;
    };
    connect();
    player.current.onBusy = () => {
      mic.current.muted = true; // AI が話している間は送らない
      setVoiceState("speaking");
    };
    player.current.onIdle = () => {
      mic.current.muted = false;
      if (voiceRef.current === "speaking") setVoiceState("listening");
    };
    return () => {
      alive = false;
      window.clearTimeout(timer);
      ws.current?.close();
      mic.current.stop();
    };
  }, [sid]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [turns]);

  function setVoiceState(s: VoiceState) {
    voiceRef.current = s;
    setVoice(s);
  }

  function onMessage(m: any) {
    switch (m.type) {
      case "voice_state":
        if (m.state === "closed" || m.state === "unavailable") {
          mic.current.stop();
          player.current.stop();
          setVoiceState(m.state);
          if (m.state === "unavailable") setBanner(t(lang, "voiceUnavailable"));
          if (m.reason === "error") setBanner(t(lang, "voiceError"));
        } else setVoiceState(m.state);
        break;
      case "transcript":
        setTurns((ts) => {
          const last = ts[ts.length - 1];
          // 途中の文字はサーバーでつなげてあるので、同じ人の未確定の吹き出しを置き換える
          if (last && last.role === m.role && !last.final) return [...ts.slice(0, -1), { role: m.role, text: m.text, final: m.final }];
          return [...ts, { role: m.role, text: m.text, final: m.final }];
        });
        break;
      case "interrupted":
        player.current.stop();
        break;
      case "notification":
        if (CARD_KINDS.has(m.kind)) setNotes((ns) => (ns.some((n) => n.id === m.id) ? ns : [...ns, m]));
        else void api.ack(m.id); // 通常の状態変化はカードを出さず、表示を更新するだけ
        void refresh();
        break;
      case "ui":
        onUi(m);
        break;
    }
  }

  function onUi(m: any) {
    switch (m.event) {
      case "expert":
        setExperts((es) => [m.expert, ...es.filter((e) => e !== m.expert)].slice(0, 4));
        break;
      case "show_items":
        setSpot(m.items);
        setTab("recommend");
        break;
      case "recommendations":
        setRecs(m.items);
        setSpot([]);
        setTab("recommend");
        break;
      case "knowledge":
        setFacts(m.facts);
        break;
      case "cart":
        setCart(m.cart);
        break;
      case "ask_guest":
        if (m.item_id) {
          if (m.options && Object.keys(m.options).length > 0) chosenOptions.current[m.item_id] = m.options;
          setSheet({ kind: "guest", item_id: m.item_id, qty: m.qty ?? 1, verdicts: m.verdicts, multi: (m.qty ?? 1) > 1 && cards[m.item_id]?.is_alcohol });
        }
        break;
      case "ask_alcohol":
        if (m.options && Object.keys(m.options).length > 0) chosenOptions.current[m.item_id] = m.options;
        setSheet({ kind: "alcohol", item_id: m.item_id, qty: m.qty ?? 1, for_guest: m.for_guest });
        break;
      case "blocked":
        setSheet({ kind: "blocked", item_id: m.item_id, check: m.check, alternatives: m.alternatives ?? [] });
        break;
      case "read_back":
        if (m.confirm_token) setSheet({ kind: "readback", data: m });
        break;
      case "order_result":
        if (m.ok) {
          setSheet(null);
          setBanner(m.message_key === "pay_first" ? t(lang, "payFirst") : t(lang, "received"));
          setTab("order");
        }
        void refresh();
        break;
      case "bill":
        setSheet({ kind: "bill", data: m });
        break;
      case "escalated":
        setBanner(m.urgent ? t(lang, "emergencySent") : t(lang, "staffNotified"));
        break;
      case "confirm_declaration":
        setSheet({ kind: "declaration", allergies: m.allergies, diets: m.diets });
        break;
      case "guests_updated":
        setGuests(m.guests);
        void api.menu(sid).then(setMenu);
        break;
      case "closed":
        location.reload();
        break;
      case "staff_coming":
        setStaffComing(true);
        setBanner(t(lang, "staffComing"));
        break;
    }
  }

  // --- 会話 ---------------------------------------------------------------
  async function startInterpret() {
    if (voiceRef.current !== "closed" && voiceRef.current !== "unavailable") ws.current?.send(JSON.stringify({ type: "voice_stop" }));
    setMode("interpret");
    player.current.unlock();
    setVoiceState("connecting");
    await mic.current.start((pcm) => {
      if (ws.current?.readyState === WebSocket.OPEN && voiceRef.current === "listening") ws.current.send(pcm);
    });
    ws.current?.send(JSON.stringify({ type: "voice_start", mode: "interpret" }));
  }

  async function tapMic() {
    setMode("reception");
    player.current.unlock();
    if (voiceRef.current === "speaking") {
      player.current.stop(); // 割り込み
      return;
    }
    if (voiceRef.current === "listening" || voiceRef.current === "connecting") {
      ws.current?.send(JSON.stringify({ type: "voice_stop" }));
      return;
    }
    setVoiceState("connecting");
    await mic.current.start((pcm) => {
      if (ws.current?.readyState === WebSocket.OPEN && voiceRef.current === "listening") ws.current.send(pcm);
    });
    ws.current?.send(JSON.stringify({ type: "voice_start" }));
  }

  function sendText(s: string) {
    if (!s.trim()) return;
    player.current.unlock();
    ws.current?.send(JSON.stringify({ type: "text", text: s }));
    setTurns((ts) => [...ts, { role: "user", text: s, final: true }]);
    setText("");
  }

  // --- タッチでの追加（AI と同じチェックを通る） ---------------------------------
  // 選択肢（サイズ・麺の硬さなど）を選んだ結果。どなたの分・お酒の確認のあとも引き継ぐ
  const chosenOptions = useRef<Record<string, Record<string, string>>>({});
  function startAdd(item_id: string) {
    if ((cards[item_id]?.options ?? []).length > 0) setSheet({ kind: "options", item_id, qty: 1 });
    else void addItem(item_id);
  }

  async function addItem(item_id: string, qty = 1, for_guest: string | null = null) {
    const r = await api.addCart(sid, item_id, qty, for_guest, undefined, chosenOptions.current[item_id]);
    handleCheck(item_id, qty, r.check, r.alternatives ?? []);
    setCart(r.cart);
  }

  function handleCheck(item_id: string, qty: number, check: Check, alternatives: Card[]) {
    switch (check.decision) {
      case "ok":
        setSheet(null);
        break;
      case "need_guest":
        setSheet({ kind: "guest", item_id, qty, verdicts: check.verdicts, multi: check.reasons.includes("one_per_guest") });
        break;
      case "need_alcohol_check":
        setSheet({ kind: "alcohol", item_id, qty, for_guest: check.for_guest ?? guests[0]?.id });
        break;
      default:
        setSheet({ kind: "blocked", item_id, check, alternatives });
    }
  }

  // 一人一つずつ追加し、お酒の確認が必要な人を順番に聞く
  const [alcoholQueue, setAlcoholQueue] = useState<{ item_id: string; guests: string[] } | null>(null);
  async function addForGuests(item_id: string, ids: string[]) {
    const r = await api.addCart(sid, item_id, 1, null, ids, chosenOptions.current[item_id]);
    setCart(r.cart);
    const pending = (r.checks ?? [r.check]).filter((c) => c.decision === "need_alcohol_check").map((c) => c.for_guest!);
    if (pending.length) {
      setAlcoholQueue({ item_id, guests: pending.slice(1) });
      setSheet({ kind: "alcohol", item_id, qty: 1, for_guest: pending[0] });
    } else handleCheck(item_id, 1, r.check, r.alternatives ?? []);
  }

  async function answerAlcohol(guest_id: string, adult: boolean, driving: boolean, item_id: string, qty: number) {
    await api.alcohol(sid, guest_id, adult, driving);
    await refresh();
    if (adult && !driving) await addItem(item_id, qty, guest_id);
    else setSheet(null);
    if (alcoholQueue && alcoholQueue.guests.length) {
      const [next, ...rest] = alcoholQueue.guests;
      setAlcoholQueue({ item_id: alcoholQueue.item_id, guests: rest });
      setSheet({ kind: "alcohol", item_id: alcoholQueue.item_id, qty: 1, for_guest: next });
    } else setAlcoholQueue(null);
  }

  async function readBack() {
    const rb = await api.readBack(sid);
    setSheet({ kind: "readback", data: rb });
  }

  async function submit(token: string) {
    const r = await api.submit(sid, token);
    if (r.ok) {
      setSheet(null);
      setBanner(r.message_key === "pay_first" ? t(lang, "payFirst") : t(lang, "received"));
      setTab("order");
    } else setBanner(r.reason);
    await refresh();
  }

  const guestLabel = (id: string | null) => guests.find((g) => g.id === id)?.label ?? "";
  const cartTotal = cart.reduce((s, l) => s + (cards[l.item_id]?.price ?? 0) * l.qty, 0);

  // カートの交差推薦：料理はあるのに飲み物がないとき、全員が ○ の飲み物を 1 つ提案する
  const [drink, setDrink] = useState<Card | null>(null);
  const hasDrink = cart.some((l) => cards[l.item_id]?.category === "drink");
  useEffect(() => {
    if (cart.length === 0 || hasDrink) {
      setDrink(null);
      return;
    }
    void api.recommend(sid, "drink").then((rs) => setDrink(rs.find((r) => Object.values(r.marks).every((m) => m.mark === "○")) ?? null));
  }, [cart.length, hasDrink]);

  return (
    <div className={`table-screen${bigText ? " big-text" : ""}`}>
      <section className="left">
        {/* ゲストごとの食事の確認状態（AI が会話で聞いて記録すると更新される） */}
        <div className="guest-status">
          {guests.map((g) => (
            <span key={g.id} className={g.declared ? "chip on" : "chip pending"}>
              {g.label}：{g.declared
                ? [...g.allergies.map((a) => allergenName(lang, a)), ...g.diets.map((d) => DIETS.find((x) => x.code === d)?.[lang] ?? d)].join("・") || t(lang, "noRestriction")
                : t(lang, "undeclared")}
            </span>
          ))}
        </div>
        <div className="experts">
          {experts.map((e) => (
            <span key={e} className={`badge ${e}`}>
              {expertLabel(lang, e)}
              {e === "allergy" ? " ✓" : ""}
            </span>
          ))}
        </div>
        <div className="log" ref={logRef}>
          {turns.map((tn, i) => (
            <div key={i} className={`bubble ${tn.role}`}>
              {tn.text}
            </div>
          ))}
          {facts.length > 0 && <div className="facts">{facts.join(" / ")}</div>}
        </div>
        <div className="quick">
          <button className="chip" onClick={() => sendText(t(lang, "quickRecommend"))}>{t(lang, "quickRecommend")}</button>
          <button className="chip" onClick={() => sendText(t(lang, "quickAllergy"))}>{t(lang, "quickAllergy")}</button>
          <button className="chip" onClick={() => setTab("menu")}>{t(lang, "quickOrder")}</button>
          <button className="chip" onClick={async () => setSheet({ kind: "bill", data: await api.bill(sid) })}>{t(lang, "quickBill")}</button>
        </div>
        {!hasVoice(lang) && <div className="note small">{t(lang, "textOnly")}</div>}
        <div className="input-row">
          {hasVoice(lang) && (
            <button className={`mic ${voice}`} onClick={tapMic}>
              {voice === "closed" || voice === "unavailable" ? t(lang, "talk") : t(lang, voice === "speaking" ? "speaking" : voice === "connecting" ? "connecting" : "listening")}
            </button>
          )}
          <input value={text} placeholder={t(lang, "typeHere")} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && sendText(text)} />
          <button onClick={() => sendText(text)}>{t(lang, "send")}</button>
        </div>
        {staffComing && (
          <button className={mode === "interpret" && voice !== "closed" ? "interpret on" : "interpret"} onClick={startInterpret}>
            {t(lang, "interpret")}
          </button>
        )}
        <button className="emergency" onClick={async () => { await api.emergency(sid); setBanner(t(lang, "emergencySent")); }}>
          {t(lang, "emergency")}
        </button>
        <button className={bigText ? "big-toggle on" : "big-toggle"} aria-pressed={bigText} onClick={toggleBigText}>
          {t(lang, "bigText")}
        </button>
      </section>

      <section className="right">
        <div className="tabs">
          {(["recommend", "menu", "order"] as const).map((k) => (
            <button key={k} className={tab === k ? "tab on" : "tab"} onClick={() => setTab(k)}>
              {t(lang, k === "recommend" ? "tabRecommend" : k === "menu" ? "tabMenu" : "tabOrder")}
              {k === "order" && cart.length > 0 ? ` (${cart.reduce((s, l) => s + l.qty, 0)})` : ""}
            </button>
          ))}
          <span className="legend">{t(lang, "markLegend")}</span>
        </div>
        {notes.length > 0 && (
          <div className="notes">
            {notes.map((n) => (
              <div key={n.id} className={`note-card ${n.kind}`}>
                <span>{noteText(lang, n, cards)}</span>
                <button onClick={() => { void api.ack(n.id); setNotes((ns) => ns.filter((x) => x.id !== n.id)); }}>{t(lang, "close")}</button>
              </div>
            ))}
          </div>
        )}
        {offline && <div className="banner warn">{t(lang, "reconnecting")}</div>}
        {banner && <div className="banner" onClick={() => setBanner("")}>{banner}</div>}
        <div className="cards">
          {tab === "recommend" && [...spot, ...recs.filter((r) => !spot.some((s) => s.item_id === r.item_id))].map((c) => (
            <ItemCard key={c.item_id} c={c} lang={lang} guests={guests} onAdd={() => startAdd(c.item_id)} />
          ))}
          {tab === "menu" && menu.map((c) => <ItemCard key={c.item_id} c={c} lang={lang} guests={guests} onAdd={() => startAdd(c.item_id)} />)}
          {tab === "order" && (
            <div className="order-tab">
              <h3>{t(lang, "cart")}</h3>
              {cart.map((l) => (
                <div key={l.line_id} className="cart-line">
                  <span>{cards[l.item_id]?.name ?? l.item_id}</span>
                  {Object.entries(l.options ?? {}).map(([k, v]) => {
                    const g = cards[l.item_id]?.options?.find((o) => o.key === k);
                    return <span key={k} className="small">{g?.choices.find((c) => c.key === v)?.label ?? v}</span>;
                  })}
                  {l.for_guest && <span className="who">{guestLabel(l.for_guest)}</span>}
                  <span className="qty">
                    <button onClick={async () => { const r = await api.updateCart(sid, l.line_id, l.qty - 1); setCart(r.cart); }}>−</button>
                    {l.qty}
                    <button onClick={async () => { const r = await api.updateCart(sid, l.line_id, l.qty + 1); setCart(r.cart); }}>＋</button>
                  </span>
                </div>
              ))}
              {orders.map((o) =>
                o.lines.map((l: any) => (
                  <div key={l.line_id} className={`order-line ${l.status}`}>
                    <span>{l.name[lang] || l.name.ja} ×{l.qty}</span>
                    {l.for_guest && <span className="who">{guestLabel(l.for_guest)}</span>}
                    <span className="status">{statusLabel(lang, l.status)}</span>
                    {(l.status === "accepted" || l.status === "confirmed") && (
                      <button className="link" onClick={async () => { await api.cancelLine(o.id, l.line_id); await refresh(); }}>✕</button>
                    )}
                  </div>
                )),
              )}
            </div>
          )}
        </div>
        {drink && (
          <div className="cross-sell">
            <span>{t(lang, "drinkSuggest")}</span>
            <b>{drink.name}</b>
            {drink.reasons?.[0] && <span className="small">{drink.reasons[0]}</span>}
            <button onClick={() => startAdd(drink.item_id)}>{t(lang, "add")}</button>
          </div>
        )}
        <div className="cart-bar">
          <span>{t(lang, "cart")}：{cart.reduce((s, l) => s + l.qty, 0)} · ¥{cartTotal.toLocaleString()}</span>
          <button className="primary" disabled={cart.length === 0} onClick={readBack}>{t(lang, "orderNow")}</button>
        </div>
      </section>

      {sheet && (
        <div className="sheet-backdrop" onClick={() => setSheet(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            {sheet.kind === "options" && (
              <OptionsSheet card={cards[sheet.item_id]} onDone={(opts) => { chosenOptions.current[sheet.item_id] = opts; void addItem(sheet.item_id, sheet.qty); }} />
            )}
            {sheet.kind === "guest" && sheet.multi && (
              <MultiGuest lang={lang} guests={guests} qty={sheet.qty} title={cards[sheet.item_id]?.name ?? ""} onDone={(ids) => addForGuests(sheet.item_id, ids)} />
            )}
            {sheet.kind === "guest" && !sheet.multi && (
              <>
                <h3>{t(lang, "whoFor")} — {cards[sheet.item_id]?.name}</h3>
                <div className="chips">
                  {guests.map((g) => {
                    const v = sheet.verdicts?.[g.id];
                    return (
                      <button key={g.id} className="chip big" onClick={() => addItem(sheet.item_id, sheet.qty, g.id)}>
                        {g.label} {v ? v.mark : ""}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
            {sheet.kind === "alcohol" && (
              <AlcoholSheet lang={lang} guest={guests.find((g) => g.id === sheet.for_guest)} onAnswer={(adult, driving) => answerAlcohol(sheet.for_guest, adult, driving, sheet.item_id, sheet.qty)} />
            )}
            {sheet.kind === "blocked" && (
              <>
                <h3>{sheet.check.decision === "need_staff" ? t(lang, "needStaff") : t(lang, "blocked")}</h3>
                <p>{cards[sheet.item_id]?.name}：{(sheet.check.for_guest && cards[sheet.item_id]?.marks[sheet.check.for_guest]?.reasons.join("、")) || sheet.check.reasons.join(", ")}</p>
                {sheet.check.decision === "need_staff" && (
                  <button className="chip" onClick={async () => { await api.askStaff(sid, sheet.item_id, sheet.check.for_guest); setBanner(t(lang, "staffWillCheck")); setSheet(null); }}>
                    {t(lang, "askStaff")}
                  </button>
                )}
                {sheet.alternatives.length > 0 && <h4>{t(lang, "alternatives")}</h4>}
                <div className="cards small">
                  {sheet.alternatives.map((c) => (
                    <ItemCard key={c.item_id} c={c} lang={lang} guests={guests} onAdd={() => addItem(c.item_id, 1, sheet.check.for_guest)} />
                  ))}
                </div>
              </>
            )}
            {sheet.kind === "readback" && (
              <>
                <h3>{t(lang, "confirmTitle")}</h3>
                {sheet.data.lines.map((l: any) => (
                  <div key={l.line_id} className="rb-line">
                    <b>{l.name} ×{l.qty}</b> {l.for_guest && <span className="who">{guestLabel(l.for_guest)}</span>} ¥{l.amount.toLocaleString()}
                    <div className="small">
                      {l.allergens.map((a: string) => allergenName(lang, a)).join("・")}
                      {l.cross_contact.length > 0 && ` （${t(lang, "mayContact")}：${l.cross_contact.map((a: string) => allergenName(lang, a)).join("・")}）`}
                    </div>
                  </div>
                ))}
                {sheet.data.problems?.length > 0 && <p className="warn">{JSON.stringify(sheet.data.problems.map((p: any) => p.reasons))}</p>}
                <div className="total">{t(lang, "total")} ¥{sheet.data.total.toLocaleString()}</div>
                {sheet.data.wait_min >= 15 && (
                  <p className="note">{t(lang, "waitNotice").replace("{n}", String(sheet.data.wait_min))}</p>
                )}
                <div className="row">
                  <button onClick={() => setSheet(null)}>{t(lang, "fix")}</button>
                  <button className="primary" disabled={!sheet.data.confirm_token} onClick={() => submit(sheet.data.confirm_token)}>{t(lang, "sendOrder")}</button>
                </div>
              </>
            )}
            {sheet.kind === "bill" && (
              <>
                <h3>{t(lang, "billTitle")}</h3>
                {sheet.data.lines.map((l: any) => (
                  <div key={l.line_id} className="rb-line">{l.name[lang] || l.name.ja} ×{l.qty} ¥{l.amount.toLocaleString()}</div>
                ))}
                {sheet.data.charges.map((c: any, i: number) => (
                  <div key={i} className="rb-line">{c.label[lang] || c.label.ja} ×{c.qty} ¥{c.amount.toLocaleString()}</div>
                ))}
                <div className="total">{t(lang, "total")} ¥{sheet.data.total.toLocaleString()} · {t(lang, "perPerson")} ¥{sheet.data.per_person.toLocaleString()}</div>
                {sheet.data.per_guest && Object.keys(sheet.data.per_guest).length > 0 && (
                  <div className="small">
                    <div>{t(lang, "perGuest")}</div>
                    {Object.entries(sheet.data.per_guest as Record<string, number>).map(([gid, amt]) => (
                      <div key={gid}>{guestLabel(gid) || gid}：¥{amt.toLocaleString()}</div>
                    ))}
                  </div>
                )}
                <p className="note">{t(lang, "payAtRegister")}</p>
              </>
            )}
            {sheet.kind === "declaration" && (
              <>
                <h3>{t(lang, "registerAllergy")}</h3>
                <p>{[...sheet.allergies.map((a) => allergenName(lang, a)), ...sheet.diets].join("・")}</p>
                <div className="chips">
                  {guests.map((g) => (
                    <button key={g.id} className="chip big" onClick={async () => {
                      await api.declare(sid, [{ guest_id: g.id, allergies: [...g.allergies, ...sheet.allergies], diets: [...g.diets, ...sheet.diets], severe: g.severe }]);
                      await refresh();
                      void api.menu(sid).then(setMenu);
                      setSheet(null);
                    }}>{g.label}</button>
                  ))}
                </div>
              </>
            )}
            <button className="link close" onClick={() => setSheet(null)}>{t(lang, "close")}</button>
          </div>
        </div>
      )}
      <div className="demo-clock">{t(lang, "demoClock")}</div>
    </div>
  );
}

function OptionsSheet({ card, onDone }: { card?: Card; onDone: (opts: Record<string, string>) => void }) {
  const [sel, setSel] = useState<Record<string, string>>(() => Object.fromEntries((card?.options ?? []).map((g) => [g.key, g.default])));
  if (!card) return null;
  const delta = (card.options ?? []).reduce((s, g) => s + (g.choices.find((c) => c.key === sel[g.key])?.price_delta ?? 0), 0);
  return (
    <>
      <h3>{card.name}</h3>
      {(card.options ?? []).map((g) => (
        <div key={g.key}>
          <p>{g.label}</p>
          <div className="chips">
            {g.choices.map((c) => (
              <button key={c.key} className={sel[g.key] === c.key ? "chip big on" : "chip big"} onClick={() => setSel((s) => ({ ...s, [g.key]: c.key }))}>
                {c.label}{c.price_delta ? ` +¥${c.price_delta}` : ""}
              </button>
            ))}
          </div>
        </div>
      ))}
      <button className="primary" onClick={() => onDone(sel)}>OK · ¥{(card.price + delta).toLocaleString()}</button>
    </>
  );
}

function MultiGuest({ lang, guests, qty, title, onDone }: { lang: Lang; guests: Guest[]; qty: number; title: string; onDone: (ids: string[]) => void }) {
  const [sel, setSel] = useState<string[]>([]);
  return (
    <>
      <h3>{t(lang, "whoFor")} — {title} ×{qty}</h3>
      <p className="note">{t(lang, "onePerPerson")}</p>
      <div className="chips">
        {guests.map((g) => (
          <button key={g.id} className={sel.includes(g.id) ? "chip big on" : "chip big"} onClick={() => setSel((s) => (s.includes(g.id) ? s.filter((x) => x !== g.id) : [...s, g.id]))}>{g.label}</button>
        ))}
      </div>
      <button className="primary" disabled={sel.length === 0} onClick={() => onDone(sel)}>OK ({sel.length})</button>
    </>
  );
}

function AlcoholSheet({ lang, guest, onAnswer }: { lang: Lang; guest?: Guest; onAnswer: (adult: boolean, driving: boolean) => void }) {
  const [adult, setAdult] = useState<boolean | null>(null);
  const [driving, setDriving] = useState<boolean | null>(null);
  return (
    <>
      <h3>{t(lang, "alcoholTitle")} — {guest?.label}</h3>
      <p>{t(lang, "isAdult")}</p>
      <div className="row">
        <button className={adult === true ? "chip on" : "chip"} onClick={() => setAdult(true)}>{t(lang, "yes")}</button>
        <button className={adult === false ? "chip on" : "chip"} onClick={() => setAdult(false)}>{t(lang, "no")}</button>
      </div>
      <p>{t(lang, "isDriving")}</p>
      <div className="row">
        <button className={driving === true ? "chip on" : "chip"} onClick={() => setDriving(true)}>{t(lang, "yes")}</button>
        <button className={driving === false ? "chip on" : "chip"} onClick={() => setDriving(false)}>{t(lang, "no")}</button>
      </div>
      <button className="primary" disabled={adult === null || driving === null} onClick={() => onAnswer(adult!, driving!)}>OK</button>
    </>
  );
}

function ItemCard({ c, lang, guests, onAdd }: { c: Card; lang: Lang; guests: Guest[]; onAdd: () => void }) {
  return (
    <div className={`card ${c.sold_out ? "sold-out" : ""}`}>
      <div className="photo">
        <img src={c.photo_url} alt={c.name} />
        {c.photo_is_generated && <span className="image-tag">{t(lang, "image")}</span>}
      </div>
      <div className="body">
        <div className="name">{c.name}</div>
        {lang !== "ja" && <div className="name-ja">{c.name_ja}</div>}
        <div className="price">¥{c.price.toLocaleString()}</div>
        {c.reasons && c.reasons.length > 0 && <div className="reasons">{c.reasons.map((r) => <span key={r}>{r}</span>)}</div>}
        {c.portion && <div className="small">{c.portion}</div>}
        <div className="marks">
          {guests.map((g) => (
            <span key={g.id} className={`mark m${c.marks[g.id]?.mark}`} title={c.marks[g.id]?.reasons.join("、")}>
              {g.id.toUpperCase()} {c.marks[g.id]?.mark}
            </span>
          ))}
        </div>
        <button className="add" disabled={c.sold_out} onClick={onAdd}>{c.sold_out ? t(lang, "soldOut") : t(lang, "add")}</button>
      </div>
    </div>
  );
}

function noteText(lang: Lang, n: any, cards: Record<string, Card>): string {
  const name = cards[n.payload?.item_id]?.name ?? "";
  const key = { sold_out: "noteSoldOut", rejected: "noteRejected", last_order: "noteLastOrder" }[n.kind as string] ?? "noteUpdated";
  return t(lang, key as Parameters<typeof t>[1]).replace("{name}", name);
}
