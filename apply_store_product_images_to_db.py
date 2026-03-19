from pathlib import Path


DATA_PATHS = (
    Path("dataset") / "store_products.json",
    Path("dataset") / "disease_store_products.json",
)


def main() -> None:
    from app import app, db, StoreProduct

    data = []
    for data_path in DATA_PATHS:
        if not data_path.exists():
            raise FileNotFoundError(f"Missing: {data_path}")

        raw_rows = app.json.loads(data_path.read_text(encoding="utf-8"))
        if not isinstance(raw_rows, list):
            raise ValueError(f"{data_path.name} must be a list")
        data.extend(raw_rows)

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
