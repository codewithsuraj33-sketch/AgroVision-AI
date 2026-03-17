import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps  # type: ignore


ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "dataset" / "store_products.json"
OVERRIDE_PATH = ROOT / "dataset" / "store_products_override.json"
DB_PATH = ROOT / "instance" / "database.db"
PRODUCTS_DIR = ROOT / "static" / "products"


def load_json_list(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_json_list(path: Path, items):
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ensure_image_file(image_url: str, label: str) -> None:
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    url = str(image_url or "").strip()
    if not url.startswith("/static/products/"):
        return
    filename = url.split("/static/products/", 1)[1].strip().lstrip("/")
    if not filename:
        return
    dst = PRODUCTS_DIR / filename
    if dst.exists():
        return

    # Common typo fix: wheelbarroe.jpg exists in this repo.
    if filename == "wheelbarrow.jpg":
        typo = PRODUCTS_DIR / "wheelbarroe.jpg"
        if typo.exists():
            dst.write_bytes(typo.read_bytes())
            return

    # Generate a clean placeholder so UI doesn't show a broken image.
    size = 900
    img = Image.new("RGB", (size, size), (12, 30, 60))
    draw = ImageDraw.Draw(img)

    # Background shapes
    draw.ellipse((size * 0.62, -size * 0.12, size * 1.25, size * 0.5), fill=(20, 120, 88))
    draw.ellipse((-size * 0.18, size * 0.66, size * 0.38, size * 1.22), fill=(40, 95, 160))
    draw.rounded_rectangle((40, 40, size - 40, size - 40), radius=48, outline=(220, 235, 255), width=3)

    title = (label or filename.rsplit(".", 1)[0]).strip()[:32]
    subtitle = filename

    # Font fallback
    try:
        font_big = ImageFont.truetype("arial.ttf", 54)
        font_small = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((70, 110), title, fill=(245, 250, 255), font=font_big)
    draw.text((70, 190), subtitle, fill=(190, 210, 235), font=font_small)
    draw.text((70, size - 120), "AgroVision Store", fill=(160, 190, 220), font=font_small)

    img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS)
    img.save(dst, format="JPEG", quality=86, optimize=True, progressive=True)


def merge_dataset(existing, overrides):
    by_id = {}
    for item in existing:
        try:
            by_id[int(item.get("id"))] = dict(item)
        except Exception:
            continue

    for item in overrides:
        try:
            pid = int(item.get("id"))
        except Exception:
            continue
        current = by_id.get(pid, {"id": pid})
        for key in ("name", "price", "category", "image", "description", "rating"):
            if key in item:
                current[key] = item[key]
        by_id[pid] = current

    merged = list(by_id.values())
    merged.sort(key=lambda x: int(x.get("id") or 0))
    return merged


def apply_db(overrides):
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}")
    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        # Ensure table exists.
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='store_product'")
        if not cur.fetchone():
            raise SystemExit("store_product table not found. Run app.py once to initialize DB.")

        for item in overrides:
            pid = int(item["id"])
            name = str(item.get("name") or "").strip()
            price = int(item.get("price") or 0)
            category = str(item.get("category") or "Organic").strip() or "Organic"
            image_url = str(item.get("image") or "").strip()
            description = str(item.get("description") or "").strip()
            try:
                rating = float(item.get("rating") or 4.2)
            except Exception:
                rating = 4.2

            # Update only if row exists; otherwise we leave seeding logic to create.
            cur.execute("SELECT id FROM store_product WHERE id=?", (pid,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    UPDATE store_product
                    SET name=?, price=?, category=?, image_url=?, description=?, rating=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (name, price, category, image_url, description, rating, pid),
                )
        con.commit()
    finally:
        con.close()


def main():
    if not OVERRIDE_PATH.exists():
        raise SystemExit(f"Missing override file: {OVERRIDE_PATH}")
    overrides = load_json_list(OVERRIDE_PATH)
    if not overrides:
        raise SystemExit("Override file is empty or invalid.")

    # Ensure images exist (or placeholders).
    for item in overrides:
        ensure_image_file(item.get("image", ""), item.get("name", ""))

    # Merge dataset file (keeps any extra keys like seller/unit/tags for other IDs).
    existing = load_json_list(DATASET_PATH) if DATASET_PATH.exists() else []
    merged = merge_dataset(existing, overrides)
    save_json_list(DATASET_PATH, merged)

    # Apply to DB (existing rows only).
    apply_db(overrides)

    print(f"Applied overrides: {len(overrides)} items.")
    print(f"Updated dataset: {DATASET_PATH}")
    print(f"Updated DB: {DB_PATH}")


if __name__ == "__main__":
    main()

