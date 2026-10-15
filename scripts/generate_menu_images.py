"""デモ店の料理のイメージ画像を生成する（画面には「イメージ」と表示する）。

使い方：
  cd backend && uv run python ../scripts/generate_menu_images.py            # まだない画像だけ作る
  cd backend && uv run python ../scripts/generate_menu_images.py --only f_karaage --force
出力：backend/media/menu/<料理 ID>.jpg（幅 640、JPEG）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google import genai  # noqa: E402
from google.genai import errors  # noqa: E402
from google.genai import types  # noqa: E402

from app.config import settings  # noqa: E402
from app.seed.izakaya import build_menu  # noqa: E402

MODEL = "gemini-nano-banana-2.1"
PACE_S = 20  # 1 枚ごとの間隔（秒）
OUT = REPO_ROOT / "backend" / "media" / "menu"
STYLE = ("Realistic food photo at a small Japanese izakaya, warm lighting, dark wooden counter, "
         "45-degree angle, the dish fills the frame, no text, no letters, no logos, no people.")
HINTS = {  # 料理の見た目の補足（量や器）
    "d_beer": "a medium glass mug of draft beer with foam",
    "d_highball": "a tall glass of whisky highball with ice and lemon",
    "d_sake": "a small ceramic sake flask (tokkuri) and cup",
    "d_yuzu_sour": "a glass of yuzu citrus sour with ice",
    "d_oolong": "a glass of iced oolong tea",
    "d_yuzu_soda": "a glass of non-alcoholic yuzu soda with ice",
    "s_edamame": "a small bowl of salted green edamame pods",
    "s_hiyayakko": "a block of chilled silken tofu topped with grated ginger, green onion and bonito flakes",
    "s_cucumber": "smashed cucumber with sesame oil and garlic on a small plate",
    "s_dashimaki": "a rolled Japanese dashi omelette sliced on a plate with grated daikon",
    "f_karaage": "five pieces of Japanese fried chicken with a lemon wedge",
    "f_ebi_ten": "three shrimp tempura with tentsuyu dipping sauce",
    "f_fries": "a plate of plain salted french fries, no dipping sauce",
    "f_agedashi": "deep-fried tofu in dashi broth topped with grated daikon and green onion",
    "g_yakitori": "five salted grilled chicken yakitori skewers, chicken only (no leek, no lemon)",
    "g_saba": "a salt-grilled mackerel fillet with grated daikon",
    "g_buta_kimchi": "stir-fried pork and kimchi on a plate",
    "g_mapo": "a bowl of spicy mapo tofu",
    "g_maguro": "tuna sashimi slices with wasabi and shiso leaf",
    "r_ochazuke": "a bowl of rice topped with a piece of grilled salmon and nori, with green tea poured over",
    "r_onigiri": "one grilled triangular rice ball brushed with soy sauce",
    "ds_matcha_ice": "a single plain scoop of matcha green tea ice cream in a small bowl, nothing else",
    "g_sanma": "one whole salt-grilled Pacific saury on a long plate with grated daikon and a halved sudachi",
    "g_chanchan": "salmon fillet with cabbage and onion steamed with miso and butter in a small iron pan",
    "f_kinoko_ten": "maitake and shiitake mushroom tempura on paper with a little salt",
    "s_satoimo": "small taro potatoes simmered in glossy sweet soy broth in a small bowl",
    "s_ginnan": "about ten roasted ginkgo nuts with salt on a small plate",
    "r_kurigohan": "a bowl of rice cooked with chestnuts",
    "ds_kabocha": "a smooth pumpkin custard pudding in a small glass cup",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    client = genai.Client(vertexai=True, project=settings.google_cloud_project, location="global")
    for item in build_menu().values():
        if args.only and item.id not in args.only:
            continue
        dest = OUT / f"{item.id}.jpg"
        if dest.exists() and not args.force:
            continue
        # 店主が答えた食材にないものを描かない（写真に写った具がアレルギーの判断を誤らせないように）
        prompt = (f"{STYLE} The dish: {HINTS.get(item.id, item.name.en)} ({item.name.ja}). "
                  f"Show only these ingredients: {'、'.join(item.ingredients)}. No other toppings, garnish or side dishes.")
        r = None
        for attempt in range(6):  # 試用アカウントは画像モデルの 1 分あたりの上限が小さい → 429 なら待って再試行
            try:
                r = client.models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="4:3")))
                break
            except errors.ClientError as e:
                if e.code != 429:
                    raise
                print("429、60 秒待ちます", item.id, flush=True)
                time.sleep(60)
        if r is None:
            print("あきらめ:", item.id)
            continue
        time.sleep(PACE_S)
        data = next((p.inline_data.data for p in r.candidates[0].content.parts if p.inline_data and p.inline_data.data), None)
        if not data:
            print("画像なし:", item.id)
            continue
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(data)
            tmp.flush()
            # macOS の sips で縮小・JPEG に変換
            subprocess.run(["sips", "-Z", "640", "-s", "format", "jpeg", "-s", "formatOptions", "78", tmp.name, "--out", str(dest)],
                           check=True, capture_output=True)
        print("OK", item.id, dest.stat().st_size // 1024, "KB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
