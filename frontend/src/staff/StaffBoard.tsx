// 店員の端末（日本語のみ）：注文ボード（料理ごと）・引き継ぎキュー・品切れ・テーブル。
import { useEffect, useState } from "react";
import { api } from "../api";

const NEXT: Record<string, { to: string; label: string }[]> = {
  accepted: [{ to: "confirmed", label: "受ける" }, { to: "rejected", label: "断る" }],
  confirmed: [{ to: "cooking", label: "調理開始" }, { to: "rejected", label: "断る" }],
  cooking: [{ to: "served", label: "提供済み" }],
};
const STATUS_JA: Record<string, string> = { accepted: "受付", confirmed: "厨房確認", cooking: "調理中", served: "提供済み" };
const REASON_JA: Record<string, string> = {
  severe_allergy: "重いアレルギー", medical: "急病", wrong_dish: "誤配膳", complaint: "苦情", foreign_object: "異物",
  bill_dispute: "会計", dietary_check: "食事制限の確認", not_understood: "聞き取れない", abnormal_quantity: "数量", other: "その他",
};

export function StaffBoard() {
  const [board, setBoard] = useState<any>({ orders: [], escalations: [], tables: [], notifications: [] });
  const [metrics, setMetrics] = useState<any>(null);
  const [audits, setAudits] = useState<any[]>([]);
  const [menu, setMenu] = useState<any[]>([]);
  const [flash, setFlash] = useState("");
  const [say, setSay] = useState("");
  const [agent, setAgent] = useState<any>(null);

  async function load() {
    const [b, m, a] = await Promise.all([api.board(), api.metrics(), api.audit()]);
    setBoard(b);
    setMetrics(m);
    setAudits(a);
  }

  useEffect(() => {
    void load();
    const es = new EventSource("/api/staff/stream");
    es.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "notification" && (m.kind === "escalation" || m.kind === "escalation_repeat" || m.kind === "escalation_all")) {
        setFlash(m.kind === "escalation_all" ? "全員：未対応の緊急があります" : "引き継ぎがあります");
      }
      if (m.type === "notification" && m.kind === "overdue") setFlash(`${m.payload.minutes} 分以上出ていない料理があります`);
      if (m.type === "notification" && m.kind === "unpaid_bill") setFlash(m.payload?.minutes ? `${m.payload.minutes} 分以上お会計待ちのテーブルがあります` : "お会計待ちのテーブルがあります");
      void load();
    };
    return () => es.close();
  }, []);

  useEffect(() => {
    // 品切れ操作用のメニュー（最初のテーブルの画面と同じ API を使う）
    const sid = board.tables[0]?.id;
    if (sid && menu.length === 0) void api.menu(sid).then(setMenu);
  }, [board.tables]);

  const tableNo = (sid: string) => board.tables.find((t: any) => t.id === sid)?.table_no ?? "?";
  const lines = board.orders.flatMap((o: any) => o.lines.map((l: any) => ({ ...l, order_id: o.id, session_id: o.session_id, unpaid: o.prepay && !o.paid })));
  const unpaid = board.orders.filter((o: any) => o.prepay && !o.paid);
  // 注文から時間がたっても出ていない料理（見回りが通知を出す）
  const late = new Map<string, number>(board.notifications.filter((n: any) => n.kind === "overdue").map((n: any) => [n.payload.line_id, n.payload.minutes]));
  const unpaidBills = new Map<string, number>(board.notifications.filter((n: any) => n.kind === "unpaid_bill").map((n: any) => [n.payload?.session_id, n.payload?.minutes ?? 0]));

  return (
    <div className="staff">
      <header>
        <h1>NOREN 店員</h1>
        {flash && <div className="flash" onClick={() => setFlash("")}>{flash}</div>}
      </header>
      <div className="staff-cols">
        <section>
          <h2>引き継ぎ（{board.escalations.length}）</h2>
          {board.escalations.map((e: any) => (
            <div key={e.id} className={`esc ${e.urgent ? "urgent" : ""} ${e.status}`}>
              <div className="esc-head">
                <b>{e.table_no}番</b> {REASON_JA[e.reason] ?? e.reason} {e.urgent && <span className="tag">緊急</span>}
                {e.reminders > 0 && <span className="tag">再通知 {e.reminders}</span>}
              </div>
              <p>{e.summary}</p>
              <div className="row">
                {e.status === "pending" && <button className="primary" onClick={async () => { await api.setEscalation(e.id, "acknowledged"); await load(); }}>対応する</button>}
                {e.status !== "pending" && <span className="small">対応中：{e.handler}</span>}
                <button onClick={async () => { await api.setEscalation(e.id, "resolved"); await load(); }}>解決</button>
              </div>
            </div>
          ))}
        </section>
        <section>
          {unpaid.length > 0 && <h2>先払い：お支払い待ち</h2>}
          {unpaid.map((o: any) => (
            <div key={o.id} className="kline accepted">
              <b>{tableNo(o.session_id)}番</b> {o.lines.map((l: any) => `${l.name.ja}×${l.qty}`).join("、")}
              <span className="status">¥{o.lines.reduce((s: number, l: any) => s + l.unit_price * l.qty, 0).toLocaleString()}</span>
              <button className="primary" onClick={async () => { await api.markPaid(o.id); await load(); }}>支払い確認</button>
            </div>
          ))}
          <h2>注文（料理ごと）</h2>
          {lines.map((l: any) => (
            <div key={l.line_id} className={`kline ${l.status}`}>
              <b>{tableNo(l.session_id)}番</b> {l.name.ja} ×{l.qty}
              {l.for_guest && <span className="small"> {l.for_guest}</span>}
              {l.not_for?.length > 0 && <span className="tag">{l.not_for.join("・")} は召し上がらない</span>}
              {l.is_alcohol && <span className="tag">お酒・年齢を目視</span>}
              {late.has(l.line_id) && <span className="tag late">{late.get(l.line_id)} 分経過</span>}
              <span className="status">{STATUS_JA[l.status]}</span>
              <div className="row">
                {l.unpaid && <span className="tag">未払い</span>}
                {!l.unpaid && (NEXT[l.status] ?? []).map((n) => (
                  <button key={n.to} className={n.to === "rejected" ? "" : "primary"} onClick={async () => {
                    const reason = n.to === "rejected" ? prompt("断る理由") ?? "" : "";
                    await api.setLine(l.order_id, l.line_id, n.to, reason);
                    await load();
                  }}>{n.label}</button>
                ))}
              </div>
            </div>
          ))}
        </section>
        <section>
          <h2>店舗エージェント</h2>
          <div className="row">
            <input value={say} placeholder="例：出汁が切れた／今日のおすすめはまぐろ" onChange={(e) => setSay(e.target.value)}
              onKeyDown={async (e) => { if (e.key === "Enter" && say) { setAgent(await api.storeAgent(say)); setSay(""); await load(); } }} />
            <button className="primary" disabled={!say} onClick={async () => { setAgent(await api.storeAgent(say)); setSay(""); await load(); }}>伝える</button>
          </div>
          {agent && (
            <div className="conv">
              {agent.changes.length === 0 && <div>{agent.message}</div>}
              {agent.changes.map((c: any, i: number) => (
                <div key={i}>{c.knowledge ? `知識を登録：${c.knowledge}` : `${c.name}：${c.sold_out === true ? "品切れ" : c.sold_out === false ? "販売再開" : "今日のおすすめ"}`}</div>
              ))}
              {agent.pending_id ? (
                <div className="row">
                  <span className="warn">{agent.changes.length} 品に影響します。確認してください。</span>
                  <button className="primary" onClick={async () => { await api.storeAgentConfirm(agent.pending_id); setAgent({ ...agent, pending_id: null, applied: true }); await load(); }}>確定</button>
                  <button onClick={() => setAgent(null)}>取り消し</button>
                </div>
              ) : agent.applied ? <div className="small">反映しました</div> : null}
            </div>
          )}
          <h2>テーブル</h2>
          {board.tables.map((t: any) => (
            <div key={t.id} className="tbl">
              <b>{t.table_no}番</b> {t.party_size} 名 · {t.status === "billing" ? "会計中" : "食事中"}
              {unpaidBills.has(t.id) && <span className="tag">会計待ち {unpaidBills.get(t.id)} 分</span>}
              <div className="small">{t.guests.filter((g: any) => g.allergies.length || g.diets.length).map((g: any) => `${g.id}: ${[...g.allergies, ...g.diets].join("・")}`).join(" / ")}</div>
              <button onClick={async () => { await api.closeTable(t.id); await load(); }}>会計済み</button>
            </div>
          ))}
          <h2>品切れ</h2>
          <div className="soldout-list">
            {menu.map((m: any) => (
              <label key={m.item_id}>
                <input type="checkbox" checked={m.sold_out} onChange={async (e) => {
                  await api.soldOut([m.item_id], e.target.checked);
                  setMenu((ms) => ms.map((x) => (x.item_id === m.item_id ? { ...x, sold_out: e.target.checked } : x)));
                }} />
                {m.name_ja}{m.remaining !== null ? `（残り ${m.remaining}）` : ""}
              </label>
            ))}
          </div>
          <h2>数字</h2>
          {metrics && (
            <div className="small">
              <div>テーブル数：{metrics.tables}（完了：{metrics.tables_closed}）</div>
              <div>無人で完了した割合：{Math.round(metrics.unattended_rate * 100)}%</div>
              <div>引き継ぎ：{metrics.escalations} 件{Object.keys(metrics.escalations_by_reason || {}).length > 0 && `（${Object.entries(metrics.escalations_by_reason).map(([r, c]) => `${REASON_JA[r] ?? r}: ${c}`).join("、")}）`}</div>
              <div>止めた件数：{metrics.blocked} 件{Object.keys(metrics.blocked_by_reason || {}).length > 0 && `（${Object.entries(metrics.blocked_by_reason).map(([r, c]) => `${r}: ${c}`).join("、")}）`}</div>
              <div>断った料理：{metrics.lines_rejected} 件 / 取り消し：{metrics.lines_cancelled} 件</div>
              <div>AI の使用量：1 テーブルあたり {metrics.tokens_per_table?.toLocaleString()} トークン{metrics.ai_cost_yen_per_table != null && `（約 ¥${metrics.ai_cost_yen_per_table}）`}</div>
            </div>
          )}
          <h2>記録</h2>
          <div className="small">
            {audits.slice(0, 15).map((a: any, i: number) => {
              const time = a.at ? a.at.slice(11, 16) : "";
              return (
                <div key={i} className="small">
                  {[time, a.actor, a.action, a.result, a.reason].filter(Boolean).join(" · ")}
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
