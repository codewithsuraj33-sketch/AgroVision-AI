import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps  # type: ignore


PRODUCTS_DIR = Path("static") / "products"
TARGET_SIZE = 900  # pixels (square)


def normalize_to_square(path: Path, size: int) -> None:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        img.save(path, format="JPEG", quality=86, optimize=True, progressive=True)


def main() -> None:
    if not PRODUCTS_DIR.exists():
        raise SystemExit(f"Missing folder: {PRODUCTS_DIR.as_posix()}")

    images = sorted(p for p in PRODUCTS_DIR.glob("*.jpg") if p.is_file())
    if not images:
        raise SystemExit("No .jpg files found in static/products/")

    backup_dir = PRODUCTS_DIR / f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for img_path in images:
        backup_path = backup_dir / img_path.name
        shutil.copy2(img_path, backup_path)
        try:
            normalize_to_square(img_path, TARGET_SIZE)
            ok += 1
        except Exception as exc:
            # Restore original if something goes wrong for a file.
            shutil.copy2(backup_path, img_path)
            print(f"Failed: {img_path.name}: {exc}")

    print(f"Normalized {ok}/{len(images)} images to {TARGET_SIZE}x{TARGET_SIZE}.")
    print(f"Backup saved in: {backup_dir.as_posix()}")


if __name__ == "__main__":
    main()

