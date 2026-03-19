import os
import time
from pathlib import Path
from urllib.parse import quote_plus

import json

import requests  # type: ignore
from PIL import Image, ImageOps  # type: ignore


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "dataset" / "disease_data.json"
OUT_DIR = ROOT / "static" / "library" / "diseases"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_pexels_key() -> str:
    return (os.getenv("PEXELS_API_KEY") or "").strip()


def pexels_search(query: str, api_key: str) -> str | None:
    url = f"https://api.pexels.com/v1/search?query={quote_plus(query)}&per_page=1"
    res = requests.get(url, headers={"Authorization": api_key}, timeout=30)
    res.raise_for_status()
    data = res.json()
    photos = data.get("photos") or []
    if not photos:
        return None
    return ((photos[0].get("src") or {}).get("large") or "").strip() or None


def download_image(url: str, dst: Path) -> bool:
    tmp = dst.with_suffix(".tmp")
    try:
        with requests.get(url, stream=True, timeout=45) as r:
            r.raise_for_status()
            if "image/" not in (r.headers.get("Content-Type") or "").lower():
                return False
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
        tmp.replace(dst)
        return True
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def normalize_square(path: Path, size: int = 900) -> None:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        img.save(path, format="JPEG", quality=86, optimize=True, progressive=True)


def main() -> None:
    if not DATA_PATH.exists():
        raise SystemExit(f"Missing: {DATA_PATH}")

    items = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise SystemExit("disease_data.json must be a list.")

    key = get_pexels_key()
    if not key:
        raise SystemExit("Set PEXELS_API_KEY in .env to auto-fetch images.")

    changed = False
    ok = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        name = str(item.get("name") or "").strip()
        if not slug or not name:
            continue

        dst = OUT_DIR / f"{slug}.jpg"
        if not dst.exists():
            query = f"{name} crop pest disease"
            url = pexels_search(query, key)
            if not url:
                print(f"Not found: {name}")
                continue
            if download_image(url, dst):
                normalize_square(dst, 900)
                ok += 1
                print(f"Saved: {dst.as_posix()}")
            time.sleep(0.25)

        expected_url = f"/static/library/diseases/{slug}.jpg"
        if str(item.get("image") or "").strip() != expected_url:
            item["image"] = expected_url
            changed = True

    if changed:
        DATA_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Updated JSON: {DATA_PATH}")

    print(f"Done. Downloaded {ok} images.")


if __name__ == "__main__":
    main()

