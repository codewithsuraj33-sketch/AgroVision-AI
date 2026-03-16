import glob
import os

html_files = glob.glob(r'c:\Users\suraj\OneDrive\Desktop\New folder (4)\templates\*.html')

toggle_html = """
  <!-- Theme Toggle Script -->
  <button id="themeToggle" class="theme-toggle" aria-label="Toggle Theme">
    <i class="fas fa-moon" id="themeIcon"></i>
  </button>
  <script>
    (function() {
      const themeToggle = document.getElementById('themeToggle');
      const themeIcon = document.getElementById('themeIcon');
      
      // Check saved theme
      const savedTheme = localStorage.getItem('theme');
      if (savedTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
        themeIcon.classList.remove('fa-moon');
        themeIcon.classList.add('fa-sun');
      }
      
      themeToggle.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        if (currentTheme === 'dark') {
          document.documentElement.removeAttribute('data-theme');
          localStorage.setItem('theme', 'light');
          themeIcon.classList.remove('fa-sun');
          themeIcon.classList.add('fa-moon');
        } else {
          document.documentElement.setAttribute('data-theme', 'dark');
          localStorage.setItem('theme', 'dark');
          themeIcon.classList.remove('fa-moon');
          themeIcon.classList.add('fa-sun');
        }
      });
    })();
  </script>
"""

fouc_script = """  <script>
    if (localStorage.getItem('theme') === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
  </script>
"""

for file_path in html_files:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if 'themeToggle' in content:
        continue

    if '<head>' in content:
        content = content.replace('<head>', '<head>\n' + fouc_script, 1)

    if '</body>' in content:
        content = content.replace('</body>', toggle_html + '\n</body>')

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

print(f"Updated {len(html_files)} HTML files.")

css_appendix = """
/* Dark Mode Toggle Button */
.theme-toggle {
  position: fixed;
  top: 24px;
  right: 28px;
  z-index: 9999;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--button-blue-top);
  color: var(--white);
  border: none;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  transition: all 0.3s ease;
}

.theme-toggle:hover {
  transform: scale(1.1);
}

[data-theme="dark"] .theme-toggle {
  background: #1e293b;
  color: #fbbf24;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

/* Dark Mode Transitions */
body, .dash-card, .dashboard-sidebar, .card-topbar, .feature-grid, .about-panel, .glass-form, .dashboard-main {
  transition: background-color 0.5s ease, color 0.5s ease, border-color 0.5s ease, box-shadow 0.5s ease;
}

/* Dark Mode Variables */
[data-theme="dark"] {
  --bg-soft: #0f172a;
  --bg-soft-2: #1e293b;
  --ink-dark: #f1f5f9;
  --ink-mid: #cbd5e1;
  --white: #1e293b;
  --glass: rgba(15, 23, 42, 0.76);
  --glass-strong: rgba(15, 23, 42, 0.9);
  --panel-shadow: 0 30px 70px rgba(0, 0, 0, 0.5);
  --button-blue-top: #3b82f6;
  --button-blue-bottom: #2563eb;
}

/* Dark Mode General Overrides */
[data-theme="dark"] body {
  background: linear-gradient(180deg, #020617 0%, var(--bg-soft) 44%, var(--bg-soft-2) 100%);
}

[data-theme="dark"] .dashboard-sidebar {
  background: rgba(30, 41, 59, 1);
  border-right: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 4px 0 20px rgba(0,0,0,0.5);
}

[data-theme="dark"] .sidebar-link {
  color: #94a3b8;
}

[data-theme="dark"] .sidebar-link:hover,
[data-theme="dark"] .sidebar-link.is-active {
  background: rgba(255, 255, 255, 0.1);
  color: #f8fafc;
}

[data-theme="dark"] .sidebar-brand-copy span {
  color: #94a3b8;
}

[data-theme="dark"] .dash-card,
[data-theme="dark"] .single-card-wrap,
[data-theme="dark"] .about-panel,
[data-theme="dark"] .glass-form,
[data-theme="dark"] .auth-card {
  background: linear-gradient(180deg, rgba(30, 41, 59, 0.9) 0%, rgba(15, 23, 42, 0.9) 100%);
  border: 1px solid rgba(255, 255, 255, 0.05);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(18px);
}

[data-theme="dark"] .dashboard-hero h1,
[data-theme="dark"] .dashboard-hero p,
[data-theme="dark"] .weather-hero-copy h1,
[data-theme="dark"] .weather-hero-copy p,
[data-theme="dark"] .soil-hero-copy h1,
[data-theme="dark"] .soil-hero-copy p,
[data-theme="dark"] .settings-hero-copy h1,
[data-theme="dark"] .settings-hero-copy p {
  color: #e2e8f0;
}

[data-theme="dark"] .hero-chip {
  background: rgba(255, 255, 255, 0.15);
  color: #f1f5f9;
}

[data-theme="dark"] h2,
[data-theme="dark"] h3,
[data-theme="dark"] .card-title-row h2,
[data-theme="dark"] .soil-metric span,
[data-theme="dark"] .recommendation-item strong {
  color: #f1f5f9;
}

[data-theme="dark"] .weather-meta strong,
[data-theme="dark"] .weather-meta span,
[data-theme="dark"] .weather-meta small {
  color: #f1f5f9;
}

/* Moon Image Overlays for Banners */
.dashboard-hero::before,
.weather-hero-banner::before,
.soil-hero-banner::before,
.settings-hero-banner::before,
.disease-hero-banner::before,
.farms-hero-banner::before,
.alerts-hero-banner::before,
.crop-hero-banner::before,
.ai-hero-banner::before,
.farm-twin-hero-banner::before {
  content: "";
  position: absolute;
  top: 0; left: 0; width: 100%; height: 100%;
  border-radius: inherit;
  z-index: 1;
  background: 
    linear-gradient(180deg, rgba(15, 23, 42, 0) 0%, rgba(2, 6, 23, 0.95) 100%),
    url("https://images.unsplash.com/photo-1532704868953-d857effbc145?auto=format&fit=crop&w=1800&q=80") center/cover no-repeat;
  opacity: 0;
  transition: opacity 0.8s ease-in-out;
  pointer-events: none;
}

[data-theme="dark"] .dashboard-hero::before,
[data-theme="dark"] .weather-hero-banner::before,
[data-theme="dark"] .soil-hero-banner::before,
[data-theme="dark"] .settings-hero-banner::before,
[data-theme="dark"] .disease-hero-banner::before,
[data-theme="dark"] .farms-hero-banner::before,
[data-theme="dark"] .alerts-hero-banner::before,
[data-theme="dark"] .crop-hero-banner::before,
[data-theme="dark"] .ai-hero-banner::before,
[data-theme="dark"] .farm-twin-hero-banner::before {
  opacity: 1;
}

/* Base card dark mode text fixes */
[data-theme="dark"] .dashboard-hero-copy,
[data-theme="dark"] .weather-hero-copy,
[data-theme="dark"] .soil-hero-copy,
[data-theme="dark"] .settings-hero-copy,
[data-theme="dark"] .disease-hero-copy,
[data-theme="dark"] .farms-hero-copy,
[data-theme="dark"] .alerts-hero-copy,
[data-theme="dark"] .crop-hero-copy,
[data-theme="dark"] .ai-hero-copy,
[data-theme="dark"] .farm-twin-hero-copy {
  z-index: 2;
  position: relative;
}

[data-theme="dark"] .health-summary span,
[data-theme="dark"] .weather-stats div,
[data-theme="dark"] .recommendation-content span,
[data-theme="dark"] .alert-content span {
  color: #94a3b8;
}

[data-theme="dark"] .field-wrap {
  background: rgba(30, 41, 59, 0.8);
  border-color: rgba(255,255,255,0.1);
}

[data-theme="dark"] input {
  color: #f1f5f9;
}

[data-theme="dark"] input::placeholder {
  color: #64748b;
}

[data-theme="dark"] .chart-labels span {
  color: #cbd5e1;
}

[data-theme="dark"] .sidebar-logout {
  color: #94a3b8;
}

[data-theme="dark"] .sidebar-logout:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #f1f5f9;
}
"""

css_path = r'c:\Users\suraj\OneDrive\Desktop\New folder (4)\static\style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

if '.theme-toggle' not in css_content:
    with open(css_path, 'a', encoding='utf-8') as f:
        f.write(css_appendix)
    print("Appended dark mode classes to style.css")
else:
    print("Dark mode classes already in style.css")
