"""通しテスト：メニュー写真の取り込み（書き写し → 食材の質問と店主の答え → 換算 → 翻訳 → 公開）と業態の切り替え。

使い方：
  cd backend && uv run python ../scripts/e2e_menu_import.py --base http://127.0.0.1:8080 --image /path/to/menu.png
出力：logs/e2e_menu_import/<タイムスタンプ>/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

# 店主の答え（デモの台本）：料理名 → 食材。豚骨らーめんは「中華麺・豚骨・醤油・ねぎ・チャーシュー（豚肉）」
ANSWERS = {
    "濃厚豚骨らーめん": ["中華麺", "豚骨", "醤油", "ねぎ", "豚肉", "海苔"],
    "焼き餃子（5個）": ["小麦粉", "豚肉", "キャベツ", "にら", "にんにく", "ごま油", "市販のタレ"],
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--image", required=True)
    args = ap.parse_args()
    http = httpx.AsyncClient(base_url=args.base, timeout=120)
    token = (await http.post("/api/demo/start")).json()["token"]
    http.headers["x-noren-demo"] = token
    report: dict = {}

    t = time.monotonic()
    img = base64.b64encode(Path(args.image).read_bytes()).decode()
    r = (await http.post("/api/owner/menu/import", json={"image_base64": img, "mime": "image/png"})).json()
    report["transcribe_s"] = round(time.monotonic() - t, 1)
    report["transcribed"] = [(i["name"]["ja"], i["price"], i["category"]) for i in r["items"]]
    report["business_type_guess"] = r["business_type_guess"]
    print("書き写し:", report["transcribed"], r["business_type_guess"], f"{report['transcribe_s']}s")

    by_name = {i["name"]["ja"]: i for i in r["items"]}
    report["conversions"] = {}
    for name, ingredients in ANSWERS.items():
        item = by_name.get(name)
        if not item:
            print("見つからない:", name)
            continue
        q = (await http.post(f"/api/owner/menu/{item['id']}/questions")).json()
        conv = (await http.post(f"/api/owner/menu/{item['id']}/ingredients", json={"ingredients": ingredients})).json()
        tr = (await http.post(f"/api/owner/menu/{item['id']}/translate")).json()
        await http.post(f"/api/owner/menu/{item['id']}/publish", json={"name_ja": name, "price": item["price"]})
        report["conversions"][name] = {
            "questions": q["questions"], "choices": q["choices"],
            "allergens": conv["allergens"], "maybe": conv["maybe_allergens"], "tags": conv["diet_tags"],
            "status": conv["allergen_status"], "unknown": conv["unknown_ingredients"],
            "en": tr["name"]["en"], "back_translation_ja": tr["back_translation_ja"], "taste": tr["taste_tags"],
        }
        print(f"\n{name}\n  質問: {q['questions']}\n  換算: {conv['allergens']} 可能性 {conv['maybe_allergens']} "
              f"タグ {conv['diet_tags']} → {conv['allergen_status']} 不明 {conv['unknown_ingredients']}\n"
              f"  翻訳: {tr['name']['en']} / {tr['name']['zh-Hans']} / {tr['name']['ko']}\n  逆翻訳: {tr['back_translation_ja']}")

    report["store"] = (await http.patch("/api/owner/store", json={"business_type": "ramen"})).json()
    print("\n業態:", report["store"])
    out = REPO_ROOT / "logs" / "e2e_menu_import" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tonkotsu = report["conversions"].get("濃厚豚骨らーめん", {})
    gyoza = report["conversions"].get("焼き餃子（5個）", {})
    ok = (tonkotsu.get("status") == "confirmed" and "pork" in tonkotsu.get("allergens", [])
          and gyoza.get("status") == "unconfirmed" and report["store"]["payment_timing"] == "prepay")
    print("\nOK" if ok else "\nNG", out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
