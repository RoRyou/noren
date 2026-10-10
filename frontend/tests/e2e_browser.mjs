// ヘッドレスブラウザでの通しテスト（お客様の画面と店員の画面）。
// マイクは合成音声の WAV ファイルで代用する（Chrome の偽デバイス）。各段階でスクリーンショットを保存する。
//
// 使い方（バックエンドを 8080 で起動し、frontend をビルド済みのこと）：
//   node frontend/tests/e2e_browser.mjs --base http://127.0.0.1:8080 --mic /path/to/mic_test.wav
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? [...acc, [v.slice(2), a[i + 1]]] : acc), []));
const BASE = args.base ?? "http://127.0.0.1:8080";
const MIC = args.mic;
const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const OUT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../logs/e2e_browser", stamp);
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "OK " : "NG "} ${name}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch({
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    ...(MIC ? [`--use-file-for-fake-audio-capture=${MIC}`] : []),
    "--autoplay-policy=no-user-gesture-required",
    // ヘッドレスでは音声の出力先がなく、AudioContext の時計が進まない → 仮の出力で動かす
    "--disable-audio-output",
  ],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["microphone"] });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

try {
  // 1) 入座：英語・2 名・Guest 1 はえびアレルギー
  await page.goto(`${BASE}/table`);
  await page.getByText("English").click();
  await page.getByText("Tap to start").click();
  await page.locator(".party button", { hasText: "2" }).click();
  await page.getByText("Next").click();
  await page.waitForSelector(".declare-card");
  const cards = page.locator(".declare-card");
  await cards.nth(0).getByText("Shrimp", { exact: true }).click();
  await cards.nth(1).getByText("None", { exact: true }).click();
  await shot("01_declare");
  await page.getByText("Next").click();
  await page.waitForSelector(".rules li");
  const rules = await page.locator(".rules li").allTextContents();
  check("入座時にお通し・支払い方法などを表示", rules.some((r) => r.includes("¥500")) && rules.length >= 4, rules.join(" | "));
  await shot("02_notice");
  await page.getByText("Start", { exact: true }).click();

  // 2) メインの画面：おすすめのカードと、ゲストごとの判定
  await page.waitForSelector(".card");
  await page.waitForTimeout(800);
  await shot("03_main");
  await page.locator(".tab", { hasText: "Menu" }).click();
  const karaage = page.locator(".card", { hasText: "Fried chicken (karaage)" });
  const marks = await karaage.locator(".mark").allTextContents();
  check("唐揚げ：G1 △ / G2 ○（同じ油のため）", marks.join(" ").includes("G1 △") && marks.join(" ").includes("G2 ○"), marks.join(" "));
  const ebi = await page.locator(".card", { hasText: "Shrimp tempura" }).locator(".mark").allTextContents();
  check("海老天：G1 ✕", ebi.join(" ").includes("G1 ✕"), ebi.join(" "));

  // 3) タッチで追加：唐揚げ → どなたの分？ → Guest 2
  await karaage.locator("button.add").click();
  await page.waitForSelector(".sheet");
  await shot("04_who_for");
  check("唐揚げを押すと「どなたの分」が出る", await page.locator(".sheet h3", { hasText: "Who is this for?" }).isVisible());
  await page.locator(".sheet .chip", { hasText: "Guest 2" }).click();
  await page.waitForTimeout(400);

  // 4) Guest 1 の分の海老天は止まる
  await page.locator(".card", { hasText: "Shrimp tempura" }).locator("button.add").click();
  await page.locator(".sheet .chip", { hasText: "Guest 1" }).click();
  await page.waitForSelector(".sheet h3:has-text(\"can't recommend\")");
  await shot("05_blocked");
  const alts = await page.locator(".sheet .card .name").allTextContents();
  check("海老天（Guest 1）は止まり、代わりを出す（△ の唐揚げは出さない）", alts.length > 0 && !alts.some((a) => a.includes("karaage")), alts.join(", "));
  await page.locator(".sheet .close").click();

  // 5) 生ビール → Guest 2 → お酒の確認 → 追加
  await page.locator(".card", { hasText: "Draft beer" }).locator("button.add").click();
  await page.locator(".sheet .chip", { hasText: "Guest 2" }).click();
  await page.waitForSelector(".sheet h3:has-text(\"Before we serve alcohol\")");
  await shot("06_alcohol");
  const yesNo = page.locator(".sheet .row");
  await yesNo.nth(0).getByText("Yes").click();
  await yesNo.nth(1).getByText("No").click();
  await page.locator(".sheet button.primary").click();
  await page.waitForTimeout(500);
  const cartText = await page.locator(".cart-bar span").textContent();
  check("カートに 2 品（唐揚げ・生ビール）", cartText.includes("2"), cartText);

  // 6) 注文する → 確認シート（混入の可能性を表示）→ 送信
  await page.getByText("Place order").click();
  await page.waitForSelector(".rb-line");
  const rb = await page.locator(".sheet").textContent();
  check("確認シートに混入の可能性（えび）", rb.includes("may have contact") && rb.includes("Shrimp"), "");
  await shot("07_readback");
  await page.getByText("Send order").click();
  await page.waitForSelector(".banner");
  check("送信後は「受け付けました」（厨房確認前）", (await page.locator(".banner").textContent()).includes("Received"));
  await page.waitForSelector(".order-line");
  await shot("08_ordered");

  // 7) 店員の画面で受ける → お客様の画面が「厨房確認済み」に変わる
  const staff = await context.newPage();
  await staff.goto(`${BASE}/staff`);
  await staff.waitForSelector(".kline");
  await staff.screenshot({ path: path.join(OUT, "09_staff.png") });
  const alcoholTag = await staff.locator(".kline", { hasText: "生ビール" }).locator(".tag").allTextContents();
  check("店員の画面：お酒には「年齢を目視」", alcoholTag.join("").includes("年齢"), alcoholTag.join(""));
  for (const line of await staff.locator(".kline").all()) await line.getByText("受ける").click();
  await page.waitForTimeout(1500);
  const statuses = await page.locator(".order-line .status").allTextContents();
  check("お客様の画面に厨房確認が反映（通知の配信）", statuses.every((s) => s.includes("Kitchen confirmed")), statuses.join(", "));
  await shot("10_confirmed");

  // 8) 緊急ボタン → 店員の画面に緊急の引き継ぎ
  await page.locator("button.emergency").click();
  await staff.waitForSelector(".esc.urgent", { timeout: 5000 });
  check("緊急ボタンで店員に緊急の引き継ぎ", true);
  await staff.screenshot({ path: path.join(OUT, "11_staff_emergency.png") });

  // 9) 音声：マイクをタップ → 文字起こしと AI の返答（偽マイクの合成音声）
  if (MIC) {
    await page.locator("button.mic").click();
    try {
      await page.waitForSelector(".bubble.user", { timeout: 25000 });
      await page.waitForSelector(".bubble.ai", { timeout: 25000 });
      await page.waitForTimeout(4000);
      const bubbles = await page.locator(".bubble").allTextContents();
      check("音声：お客様の発話と AI の返答が表示される", bubbles.length >= 2, bubbles.join(" / ").slice(0, 300));
    } catch (e) {
      check("音声：お客様の発話と AI の返答が表示される", false, String(e).slice(0, 200));
    }
    await shot("12_voice");
  }
} catch (e) {
  check("通しテストの途中で失敗", false, String(e).slice(0, 300));
  await shot("99_error");
} finally {
  check("ページの JavaScript エラーなし", errors.length === 0, errors.join(" | ").slice(0, 300));
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(results, null, 2));
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} OK — ${OUT}`);
  await browser.close();
}
