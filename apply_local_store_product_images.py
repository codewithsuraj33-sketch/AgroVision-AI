import json
from pathlib import Path


DATA_PATH = Path("dataset") / "store_products.json"
OUT_DIR = Path("static") / "products"


CATEGORY_IMAGE_ORDER = {
    "Pesticides": [
        "fungicide.jpg",
        "neem_oil.jpg",
        "sprayer.jpg",
        "insecticide.jpg",
        "leaf_spray.jpg",
        "pest_kit.jpg",
    ],
    "Fertilizers": [
        "fertilizer.jpg",
        "npk.jpg",
        "nitrogen.jpg",
        "compost.jpg",
        "bio_fertilizer.jpg",
        "potassium.jpg",
    ],
    "Seeds": [
        "rice_seeds.jpg",
        "wheat_seeds.jpg",
        "tomato_seeds.jpg",
        "corn_seeds.jpg",
        "veg_seeds.jpg",
        "seed_kit.jpg",
    ],
    "Tools": [
        "sensor.jpg",
        "irrigation.jpg",
        "seeder.jpg",
        "sprayer_tool.jpg",
        "drip.jpg",
        "weather_sensor.jpg",
    ],
    "Organic": [
        "growth.jpg",
        "compost_pack.jpg",
        "organic_spray.jpg",
        "soil_booster.jpg",
        "eco_kit.jpg",
        "plant_care.jpg",
    ],
}


def ensure_placeholder_images(products: list[dict]) -> int:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        print("PIL not available; skipping placeholder generation.")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def pick_palette(category: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        cat = (category or "").strip().lower()
        if "pesticide" in cat:
            return (14, 25, 55), (255, 203, 69)
        if "fertilizer" in cat:
            return (8, 26, 19), (125, 255, 46)
        if "seed" in cat:
            return (7, 19, 35), (70, 196, 255)
        if "tool" in cat:
            return (7, 19, 35), (199, 214, 255)
        return (7, 19, 35), (48, 230, 191)

    def load_font(size: int):
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=size)
        except Exception:
            return ImageFont.load_default()

    title_font = load_font(42)
    small_font = load_font(18)

    created = 0
    for product in products:
        image_url = str(product.get("image") or "").strip()
        if not image_url.startswith("/static/products/"):
            continue

        file_name = image_url.split("/static/products/", 1)[-1]
        out_path = OUT_DIR / file_name
        if out_path.exists():
            continue

        bg, accent = pick_palette(str(product.get("category") or ""))
        img = Image.new("RGB", (900, 900), bg)
        draw = ImageDraw.Draw(img)

        # Soft accent blobs
        draw.ellipse((520, 520, 980, 980), fill=(accent[0], accent[1], accent[2]))
        draw.ellipse((560, 560, 980, 980), fill=bg)
        draw.ellipse((40, 40, 300, 300), fill=(accent[0], accent[1], accent[2]))

        # Header chip
        draw.rounded_rectangle((54, 60, 320, 108), radius=24, fill=(255, 255, 255))
        draw.text((74, 73), "AGROVISION STORE", fill=bg, font=small_font)

        name = str(product.get("name") or "Store Product").strip()
        desc = str(product.get("description") or "").strip()
        price = int(product.get("price") or 0)

        draw.text((56, 160), name[:28], fill=(255, 255, 255), font=title_font)
        if desc:
            draw.text((56, 220), desc[:54], fill=(230, 240, 255), font=small_font)
        if price:
            draw.rounded_rectangle((56, 780, 280, 840), radius=20, fill=(255, 255, 255))
            draw.text((78, 798), f"INR {price}", fill=bg, font=small_font)

        img.save(str(out_path), format="JPEG", quality=86, optimize=True)
        created += 1

    return created


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing: {DATA_PATH}")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("store_products.json must be a list")

    # Assign images per category order (6 each).
    per_category_index: dict[str, int] = {k: 0 for k in CATEGORY_IMAGE_ORDER}
    for item in data:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if category not in CATEGORY_IMAGE_ORDER:
            continue

        idx = per_category_index.get(category, 0)
        names = CATEGORY_IMAGE_ORDER[category]
        if idx >= len(names):
            continue

        file_name = names[idx]
        item["image"] = f"/static/products/{file_name}"
        per_category_index[category] = idx + 1

    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    created = ensure_placeholder_images([x for x in data if isinstance(x, dict)])

    # Apply to DB immediately.
    from app import app, db, StoreProduct

    updated = 0
    with app.app_context():
        for item in data:
            if not isinstance(item, dict):
                continue
            product_id = int(item.get("id") or 0)
            image_url = str(item.get("image") or "").strip()
            if product_id <= 0 or not image_url:
                continue

            product = db.session.get(StoreProduct, product_id)
            if product is None:
                continue
            product.image_url = image_url
            updated += 1

        if updated:
            db.session.commit()

    print(f"Updated {updated} products in DB; created {created} placeholder images in {OUT_DIR}.")


if __name__ == "__main__":
    main()

