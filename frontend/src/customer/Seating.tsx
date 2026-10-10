// 入座の流れ：言語 → 人数 → ゲストごとの申告（必須）→ 録音のお知らせ・お店のルール。
import { useState } from "react";
import { ALLERGENS, DIETS, LANGS, Lang, t } from "../i18n";
import { api, Guest } from "../api";

type Decl = { guest_id: string; allergies: string[]; diets: string[]; severe: boolean; none: boolean };

export function Seating({ onDone }: { onDone: (sid: string, lang: Lang) => void }) {
  const [step, setStep] = useState<"welcome" | "party" | "declare" | "notice">("welcome");
  const [lang, setLang] = useState<Lang>("ja");
  const [party, setParty] = useState(2);
  const [sid, setSid] = useState("");
  const [guests, setGuests] = useState<Guest[]>([]);
  const [decls, setDecls] = useState<Decl[]>([]);
  const [rules, setRules] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function seat() {
    setBusy(true);
    const s = await api.seat(party, lang);
    setSid(s.id);
    setGuests(s.guests);
    setDecls(s.guests.map((g) => ({ guest_id: g.id, allergies: [], diets: [], severe: false, none: false })));
    setBusy(false);
    setStep("declare");
  }

  function toggle(i: number, field: "allergies" | "diets", code: string) {
    setDecls((ds) =>
      ds.map((d, j) =>
        j !== i ? d : { ...d, none: false, [field]: d[field].includes(code) ? d[field].filter((c) => c !== code) : [...d[field], code] },
      ),
    );
  }

  const allDeclared = decls.every((d) => d.none || d.allergies.length > 0 || d.diets.length > 0);

  async function declare() {
    setBusy(true);
    await api.declare(sid, decls.map(({ guest_id, allergies, diets, severe }) => ({ guest_id, allergies, diets, severe })));
    const view = await api.view(sid);
    const store = view.store;
    const lines: string[] = [];
    if (store.otoshi_price && store.otoshi_label) {
      const label = store.otoshi_label[lang] || store.otoshi_label.en;
      lines.push(lang === "ja" ? `お通し（${store.otoshi_label.ja}）：お一人 ${store.otoshi_price} 円` : `${label}: ¥${store.otoshi_price} / person`);
    }
    for (const r of store.rules) if (r.announce) lines.push(r.text[lang] || r.text.en || r.text.ja);
    setRules(lines);
    setBusy(false);
    setStep("notice");
  }

  if (step === "welcome") {
    return (
      <div className="welcome">
        <div className="noren-cloth">のれん</div>
        <h1>{t(lang, "welcome")}</h1>
        <div className="lang-switch">
          {LANGS.map((l) => (
            <button key={l.code} className={l.code === lang ? "chip on" : "chip"} onClick={() => setLang(l.code)}>
              {l.label}
            </button>
          ))}
        </div>
        <button className="primary big" onClick={() => setStep("party")}>
          {t(lang, "tapToStart")}
        </button>
      </div>
    );
  }

  if (step === "party") {
    return (
      <div className="panel center">
        <h2>{t(lang, "howMany")}</h2>
        <div className="party">
          {[1, 2, 3, 4, 5, 6].map((n) => (
            <button key={n} className={n === party ? "chip big on" : "chip big"} onClick={() => setParty(n)}>
              {n}
            </button>
          ))}
        </div>
        <button className="primary" disabled={busy} onClick={seat}>
          {t(lang, "next")}
        </button>
      </div>
    );
  }

  if (step === "declare") {
    return (
      <div className="panel">
        <h2>{t(lang, "declareTitle")}</h2>
        <p className="note">{t(lang, "declareNote")}</p>
        <div className="declare-grid">
          {guests.map((g, i) => (
            <div key={g.id} className="declare-card">
              <h3>{g.label}</h3>
              <button
                className={decls[i].none ? "chip on" : "chip"}
                onClick={() => setDecls((ds) => ds.map((d, j) => (j === i ? { ...d, none: true, allergies: [], diets: [], severe: false } : d)))}
              >
                {t(lang, "none")}
              </button>
              <div className="chips">
                {ALLERGENS.map((a) => (
                  <button key={a.code} className={decls[i].allergies.includes(a.code) ? "chip on" : "chip"} onClick={() => toggle(i, "allergies", a.code)}>
                    {a[lang]}
                  </button>
                ))}
              </div>
              <div className="chips">
                {DIETS.map((d) => (
                  <button key={d.code} className={decls[i].diets.includes(d.code) ? "chip on" : "chip"} onClick={() => toggle(i, "diets", d.code)}>
                    {d[lang]}
                  </button>
                ))}
              </div>
              {decls[i].allergies.length > 0 && (
                <label className="severe">
                  <input
                    type="checkbox"
                    checked={decls[i].severe}
                    onChange={(e) => setDecls((ds) => ds.map((d, j) => (j === i ? { ...d, severe: e.target.checked } : d)))}
                  />
                  {t(lang, "severe")}
                </label>
              )}
            </div>
          ))}
        </div>
        <button className="primary" disabled={!allDeclared || busy} onClick={declare}>
          {t(lang, "next")}
        </button>
      </div>
    );
  }

  return (
    <div className="panel center">
      <h2>{t(lang, "privacyTitle")}</h2>
      <p className="note">{t(lang, "privacy")}</p>
      <h3>{t(lang, "rulesTitle")}</h3>
      <ul className="rules">
        {rules.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <button className="primary big" onClick={() => onDone(sid, lang)}>
        {t(lang, "agree")}
      </button>
    </div>
  );
}
