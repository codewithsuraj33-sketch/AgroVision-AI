import json
from pathlib import Path


DATA_PATH = Path("dataset") / "store_products.json"


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing: {DATA_PATH}")

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("store_products.json must be a list")

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

            current = str(product.image_url or "").strip()
            # Update if blank or placeholder SVG path.
            if (not current) or current.startswith("/static/images/store/"):
                product.image_url = image_url
                updated += 1

        if updated:
            db.session.commit()

    print(f"Updated {updated} products in DB.")


if __name__ == "__main__":
    main()

