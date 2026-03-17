import glob
import io

insert_html = '''        <a class="sidebar-link" href="/market">
          <i class="fas fa-store sidebar-icon" style="color: var(--brand-primary);"></i>
          <span>Market</span>
        </a>'''
        
target_string = '''        <a class="sidebar-link" href="/community">
          <i class="fas fa-users sidebar-icon sidebar-icon-community"></i>
          <span>Community Hub</span>
        </a>'''

html_files = glob.glob('templates/*.html')
count = 0

for f in html_files:
    if f.endswith('market.html'): continue
    
    with io.open(f, 'r', encoding='utf-8') as file:
        content = file.read()
        
    if target_string in content and insert_html not in content:
        new_content = content.replace(target_string, target_string + '\n' + insert_html)
        with io.open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
        count += 1
        
print(f'Updated {count} templates.')
