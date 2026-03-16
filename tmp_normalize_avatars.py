import os
import re

TEMPLATES_DIR = r"c:\Users\suraj\OneDrive\Desktop\New folder (4)\templates"

# The HTML block to insert
avatar_block = """          {% if user and user.profile_photo %}
            <img class="dashboard-avatar-img dashboard-avatar" style="width:42px;height:42px;border-radius:50%;object-fit:cover;margin-right:12px;" src="/static/uploads/{{ user.profile_photo }}" alt="Avatar">
          {% else %}
            <i class="fas fa-user dashboard-avatar"></i>
          {% endif %}"""

mini_avatar_block = """          {% if user and user.profile_photo %}
            <img class="dashboard-avatar-img dashboard-avatar-small" style="width:36px;height:36px;border-radius:50%;object-fit:cover;margin-right:8px;" src="/static/uploads/{{ user.profile_photo }}" alt="Avatar">
          {% else %}
            <span class="dashboard-avatar dashboard-avatar-small" aria-hidden="true"></span>
          {% endif %}"""


def normalize_avatars():
    count = 0
    for file_name in os.listdir(TEMPLATES_DIR):
        if not file_name.endswith(".html"):
            continue

        file_path = os.path.join(TEMPLATES_DIR, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        original_content = content

        # Replace standard sidebar avatar
        content = re.sub(
            r'^[ \t]*<i class="fas fa-user dashboard-avatar"></i>[ \t]*$',
            avatar_block,
            content,
            flags=re.MULTILINE
        )
        content = re.sub(
            r'^[ \t]*<span class="dashboard-avatar" aria-hidden="true"></span>[ \t]*$',
            avatar_block,
            content,
            flags=re.MULTILINE
        )

        # Replacing any instances of existing dashboard-avatar-img (which might be in ai_insights/profile/alerts from previous iterations)
        # Avoid double replacement by making sure we don't already have the block
        if "{% if user and user.profile_photo %}" not in original_content:
             # Remove existing <img> tags related to avatar in ai_insights etc
             content = re.sub(
                 r'^[ \t]*<img class="dashboard-avatar-img".*?>\n[ \t]*<i class="fas fa-user dashboard-avatar"></i>[ \t]*$',
                 avatar_block,
                 content,
                 flags=re.MULTILINE
             )
             
        # Optional: Replace the small avatar used in main content headers
        content = re.sub(
            r'^[ \t]*<span class="dashboard-avatar dashboard-avatar-small" aria-hidden="true"></span>[ \t]*$',
            mini_avatar_block,
            content,
            flags=re.MULTILINE
        )

        if content != original_content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            count += 1
            print(f"Updated {file_name}")

    print(f"\nNormalized avatars in {count} templates.")


if __name__ == "__main__":
    normalize_avatars()
