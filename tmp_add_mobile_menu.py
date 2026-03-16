import glob
import os

html_files = glob.glob(r'c:\Users\suraj\OneDrive\Desktop\New folder (4)\templates\*.html')

menu_html = """
  <!-- Mobile Menu Toggle Button -->
  <button id="mobileMenuToggle" class="mobile-menu-toggle" aria-label="Toggle Sidebar">
    <i class="fas fa-bars"></i>
  </button>
"""

# Let's insert the JS just before the closing </body> tag.
# We already have a block there for dark mode. Let's find </body> and insert before it.
menu_js = """
  <!-- Mobile Sidebar JS -->
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const mobileToggle = document.getElementById('mobileMenuToggle');
      const sidebar = document.querySelector('.dashboard-sidebar');
      
      if (mobileToggle && sidebar) {
        mobileToggle.addEventListener('click', (e) => {
          e.stopPropagation(); // prevent immediate close on document click
          sidebar.classList.toggle('sidebar-open');
        });

        // Close sidebar if clicking outside of it on mobile
        document.addEventListener('click', (e) => {
          if (window.innerWidth <= 780 && sidebar.classList.contains('sidebar-open')) {
            if (!sidebar.contains(e.target) && e.target !== mobileToggle) {
              sidebar.classList.remove('sidebar-open');
            }
          }
        });
      }
    });
  </script>
</body>
"""

for file_path in html_files:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Skip if already injected
    if 'mobile-menu-toggle' in content:
        continue

    # Insert button right after <main class="dashboard-shell"> or <body ...>
    if '<main class="dashboard-shell">' in content:
        content = content.replace('<main class="dashboard-shell">', '<main class="dashboard-shell">\n' + menu_html, 1)
    elif '<body class="dashboard-page">' in content:
        content = content.replace('<body class="dashboard-page">', '<body class="dashboard-page">\n' + menu_html, 1)
    elif '<body' in content:
        # find the end of the body tag
        body_end = int(content.find('>', content.find('<body')) + 1)
        content = content[:body_end] + '\n' + menu_html + content[body_end:] # type: ignore

    # Insert JS right before </body>, so we just replace </body> with the script and </body>
    if '</body>' in content:
        content = content.replace('</body>', menu_js, 1)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

print(f"Updated {len(html_files)} HTML files with mobile menu.")

css_appendix = """
/* ========================================================= */
/* Mobile Sidebar (Hamburger Menu)                           */
/* ========================================================= */

.mobile-menu-toggle {
  display: none;
  position: fixed;
  top: 24px;
  right: 80px; /* To the left of dark mode toggle */
  z-index: 10000;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--button-blue-top);
  color: var(--white);
  border: none;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  align-items: center;
  justify-content: center;
  font-size: 1.2rem;
  transition: all 0.3s ease;
}

[data-theme="dark"] .mobile-menu-toggle {
  background: #1e293b;
  color: #fbbf24;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.mobile-menu-toggle:hover {
  transform: scale(1.1);
}

@media (max-width: 780px) {
  .mobile-menu-toggle {
    display: flex;
  }
  
  /* Reset dashboard-shell padding so that content spreads properly when sidebar is hidden */
  .dashboard-shell {
    padding-left: 0;
  }
  
  .dashboard-page .dashboard-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    z-index: 9998;
    transform: translateX(-100%);
    transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    background-color: var(--white);
    /* For dark mode background */
  }

  [data-theme="dark"] .dashboard-sidebar {
    background-color: rgba(30, 41, 59, 1) !important;
  }

  .sidebar-open {
    transform: translateX(0) !important;
    box-shadow: 10px 0 30px rgba(0, 0, 0, 0.5) !important;
  }
  
  /* Make sure main content doesn't crash into the hidden sidebar area */
  .dashboard-main {
    width: 100%;
    margin-left: 0;
  }

  /* Add some margin to the top to avoid overlapping with fixed buttons */
  .dashboard-hero {
    padding-top: 80px; 
  }
}
"""

css_path = r'c:\Users\suraj\OneDrive\Desktop\New folder (4)\static\style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

if '.mobile-menu-toggle' not in css_content:
    with open(css_path, 'a', encoding='utf-8') as f:
        f.write(css_appendix)
    print("Appended mobile menu classes to style.css")
else:
    print("Mobile menu classes already in style.css")
