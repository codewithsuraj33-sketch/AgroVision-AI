import glob
import io


INSERT_HTML = """        <a class="sidebar-link" href="/library">
          <i class="fas fa-layer-group sidebar-icon"></i>
          <span>Library</span>
        </a>"""

TARGET_HTML = """        <a class="sidebar-link" href="/crop-library">
          <i class="fas fa-book-open sidebar-icon sidebar-icon-library"></i>
          <span>Crop Library</span>
        </a>"""


def should_skip(path: str) -> bool:
    lower = path.lower().replace("\\", "/")
    if "/templates/admin/" in lower:
        return True
    # The library pages don't use the main sidebar.
    if lower.endswith("library_home.html") or lower.endswith("library_diseases.html") or lower.endswith("library_disease_detail.html") or lower.endswith("library_tips.html") or lower.endswith("library_alerts.html"):
        return True
    return False


def main() -> None:
    html_files = glob.glob("templates/*.html")
    updated = 0

    for path in html_files:
        if should_skip(path):
            continue

        with io.open(path, "r", encoding="utf-8") as handle:
            content = handle.read()

        if INSERT_HTML in content:
            continue

        if TARGET_HTML not in content:
            continue

        new_content = content.replace(TARGET_HTML, TARGET_HTML + "\n" + INSERT_HTML)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(new_content)
        updated += 1

    print(f"Updated {updated} templates.")


if __name__ == "__main__":
    main()

