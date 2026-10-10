// 店主の画面の通しテスト：メニュー写真の取り込み → 食材の確認（換算）→ 翻訳 → 業態の切り替え。
//   node frontend/tests/e2e_owner.mjs --base http://127.0.0.1:8080 --image /path/to/menu.png
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? [...acc, [v.slice(2), a[i + 1]]] : acc), []));
const BASE = args.base ?? "http://127.0.0.1:8080";
const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const OUT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../logs/e2e_owner", stamp);
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "OK " : "NG "} ${name}${detail ? " — " + detail : ""}`);
};

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1200, height: 900 } })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
try {
  await page.goto(`${BASE}/owner`);
  await page.waitForSelector("text=NOREN 店主");
  await page.setInputFiles("input[type=file]", args.image);
  await page.waitForSelector(".banner:has-text('品を書き写しました')", { timeout: 60000 });
  const banner = await page.locator(".banner").textContent();
  check("写真から書き写し（下書き）", /\d+ 品/.test(banner), banner);
  await page.screenshot({ path: path.join(OUT, "01_drafts.png") });

  await page.locator(".owner-row", { hasText: "濃厚豚骨らーめん" }).click();
  await page.getByText("AI に質問を作ってもらう").click();
  await page.waitForSelector(".owner-detail ul li", { timeout: 60000 });
  check("AI の質問が出る（食材は決めない）", (await page.locator(".owner-detail ul li").count()) > 0);
  await page.locator(".owner-detail input[placeholder^='ほかの食材']").fill("中華麺、豚骨、醤油、ねぎ、豚肉");
  await page.getByText("この食材で確認する").click();
  await page.waitForSelector(".conv:has-text('アレルゲン（換算）')");
  const conv = await page.locator(".conv").first().textContent();
  check("換算：豚肉・小麦・大豆、卵は可能性", conv.includes("豚肉") && conv.includes("小麦") && conv.includes("含む可能性：卵"), conv);
  await page.getByText("翻訳する").click();
  await page.waitForSelector(".conv:has-text('英語：')", { timeout: 60000 });
  check("翻訳と確認用の逆翻訳", (await page.locator(".conv:has-text('英語：')").textContent()).includes("確認用"));
  await page.screenshot({ path: path.join(OUT, "02_item.png"), fullPage: true });
  await page.getByText("この内容で公開").first().click();
  await page.waitForTimeout(800);

  await page.locator(".type-switch button", { hasText: "ラーメン店" }).click();
  await page.waitForSelector(".banner:has-text('先払い')");
  check("業態をラーメン店に（先払い・お通しなし）", (await page.locator(".banner").textContent()).includes("先払い"));
  await page.screenshot({ path: path.join(OUT, "03_ramen.png") });
} catch (e) {
  check("通しテストの途中で失敗", false, String(e).slice(0, 300));
  await page.screenshot({ path: path.join(OUT, "99_error.png") });
} finally {
  check("ページの JavaScript エラーなし", errors.length === 0, errors.join(" | ").slice(0, 300));
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(results, null, 2));
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} OK — ${OUT}`);
  await browser.close();
}
