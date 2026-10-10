import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { Seating } from "./customer/Seating";
import { TableScreen } from "./customer/TableScreen";
import { Lang } from "./i18n";
import { OwnerPage } from "./owner/OwnerPage";
import { StaffBoard } from "./staff/StaffBoard";
import "./styles.css";

// 画面の切り替え：/staff = 店員、/owner = 店主、/demo = お客様と店員を並べて表示（審査用）、それ以外 = お客様
function App() {
  const [ready, setReady] = useState(false);
  const [table, setTable] = useState<{ sid: string; lang: Lang } | null>(() => {
    const saved = sessionStorage.getItem("noren_table");
    return saved ? JSON.parse(saved) : null;
  });
  const path = location.pathname;

  useEffect(() => {
    // 訪問者ごとのデモ店を用意する（cookie）
    void api.demoStart().then(() => setReady(true));
  }, []);

  if (!ready) return <div className="loading">…</div>;
  if (path.startsWith("/staff")) return <StaffBoard />;
  if (path.startsWith("/owner")) return <OwnerPage />;
  if (path.startsWith("/demo")) {
    return (
      <div className="demo-split">
        <iframe src="/table" title="お客様" />
        <iframe src="/staff" title="店員" />
        <button className="reset" onClick={async () => { await api.demoStart(true); sessionStorage.clear(); location.reload(); }}>リセット</button>
      </div>
    );
  }
  if (!table) {
    return (
      <Seating
        onDone={(sid, lang) => {
          sessionStorage.setItem("noren_table", JSON.stringify({ sid, lang }));
          setTable({ sid, lang });
        }}
      />
    );
  }
  return <TableScreen sid={table.sid} lang={table.lang} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
