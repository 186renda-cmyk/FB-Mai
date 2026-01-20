import os
import glob
import re
import copy
import json
from bs4 import BeautifulSoup, Tag

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR = os.path.join(PROJECT_ROOT, "blog")
INDEX_FILE = os.path.join(PROJECT_ROOT, "index.html")
DOMAIN = "https://fb-mai.top"

def fix_link(href, is_root_source=False):
    if not href:
        return href
    
    # 1. Remove .html suffix
    if href.endswith('.html'):
        href = href[:-5]
        
    # 2. Handle root-relative anchors
    # If the link is an anchor (#foo) and comes from root (nav/footer), 
    # it must become /#foo to work on subpages.
    # Exception: If it's just "#", leave it.
    if is_root_source and href.startswith('#') and len(href) > 1:
        href = '/' + href
        
    return href

def get_soup(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return BeautifulSoup(f, 'html.parser')

def generate_schema(metadata):
    url = f"{DOMAIN}{metadata['url']}"
    
    schema = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": metadata['title'],
        "description": metadata['description'],
        "url": url,
        "publisher": {
            "@type": "Organization",
            "name": "FBMai",
            "logo": {
                "@type": "ImageObject",
                "url": f"{DOMAIN}/favicon.svg"
            }
        }
    }

    if metadata.get('category') == 'Blog' or '/blog/' in metadata['url']:
        schema["@type"] = "BlogPosting"
        schema["headline"] = metadata['title']
        schema["datePublished"] = metadata['date']
        schema["author"] = {
            "@type": "Organization",
            "name": "FBMai"
        }
        schema["mainEntityOfPage"] = {
            "@type": "WebPage",
            "@id": url
        }
        
    return json.dumps(schema, ensure_ascii=False, indent=2)

def reconstruct_head(soup, metadata, favicons):
    head = soup.head
    if not head:
        head = soup.new_tag('head')
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.append(head)

    # Preserve
    orig_title = soup.title.string if soup.title else metadata['title']
    orig_scripts = [s for s in head.find_all('script') if s.get('src') or 'tailwind' in (s.string or "")]
    orig_links = [l for l in head.find_all('link') if 'stylesheet' in l.get('rel', [])]
    orig_styles = head.find_all('style')
    
    head.clear()
    
    # Group A
    head.append(BeautifulSoup('<meta charset="utf-8">', 'html.parser'))
    head.append(BeautifulSoup('<meta name="viewport" content="width=device-width, initial-scale=1.0">', 'html.parser'))
    title_tag = soup.new_tag('title')
    title_tag.string = orig_title
    head.append(title_tag)
    
    # Group B
    if metadata['description']:
        head.append(soup.new_tag('meta', attrs={'name': 'description', 'content': metadata['description']}))
    
    # Keywords - simplistic approach
    head.append(soup.new_tag('meta', attrs={'name': 'keywords', 'content': 'Facebook账号,FB耐用号,FB广告号,BM购买,跨境电商,FBMai'}))
    
    canonical = soup.new_tag('link', rel='canonical', href=f"{DOMAIN}{metadata['url']}")
    head.append(canonical)
    
    # Group C
    head.append(soup.new_tag('meta', attrs={'name': 'robots', 'content': 'index, follow'}))
    head.append(soup.new_tag('meta', attrs={'http-equiv': 'content-language', 'content': 'zh-CN'}))
    
    # Hreflang
    for lang, code in [('x-default', ''), ('zh', ''), ('zh-CN', '')]:
        head.append(soup.new_tag('link', rel='alternate', hreflang=lang, href=f"{DOMAIN}{metadata['url']}"))
        
    # Group D (Favicons + Resources)
    for fav in favicons:
        head.append(fav)
    for res in orig_scripts + orig_links + orig_styles:
        head.append(res)
        
    # Group E (Schema)
    script_schema = soup.new_tag('script', type='application/ld+json')
    script_schema.string = generate_schema(metadata)
    head.append(script_schema)

def extract_nav_footer_favicon(index_soup):
    # Extract Nav
    nav = index_soup.find('nav')
    if nav:
        # Create a deep copy to avoid modifying the original index soup yet
        nav = nav.__copy__()
        for a in nav.find_all('a'):
            if a.get('href'):
                a['href'] = fix_link(a['href'], is_root_source=True)
                
    # Extract Footer
    footer = index_soup.find('footer')
    if footer:
        footer = footer.__copy__()
        for a in footer.find_all('a'):
            if a.get('href'):
                a['href'] = fix_link(a['href'], is_root_source=True)

    # Extract Favicons
    favicons = []
    for link in index_soup.find_all('link'):
        rel = link.get('rel', [])
        if isinstance(rel, str): rel = [rel]
        if any(x in rel for x in ['icon', 'shortcut icon', 'apple-touch-icon']):
            link_copy = link.__copy__()
            href = link_copy.get('href', '')
            if href and not href.startswith('http') and not href.startswith('/'):
                link_copy['href'] = '/' + href
            favicons.append(link_copy)
            
    return nav, footer, favicons

def get_post_metadata(soup, filename):
    # Default values
    title = soup.title.string if soup.title else filename
    date = "2026-01-01"
    category = "Blog"
    description = ""
    
    # Try to find date/category
    # Look for div with font-bold (usually contains meta)
    meta_div = soup.find('div', class_=re.compile(r'font-bold'))
    if meta_div:
        spans = meta_div.find_all('span')
        for s in spans:
            text = s.text.strip()
            # Check if it looks like a date
            if re.match(r'\d{4}-\d{2}-\d{2}', text):
                date = text
            # Check if it looks like a category (not a dot, not empty, not the date)
            elif text and text != '•' and not re.match(r'\d{4}-\d{2}-\d{2}', text):
                category = text
            
    # Description
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc:
        description = meta_desc.get('content', '')
        
    return {
        'title': title,
        'date': date,
        'category': category,
        'description': description,
        'filename': filename,
        'url': f"/blog/{filename.replace('.html', '')}",
        'soup': soup
    }

def create_article_card(soup, post):
    a = soup.new_tag('a', href=post['url'], **{'class': "group block glass-card rounded-2xl overflow-hidden hover:border-fbBlue/50 transition-all"})
    
    # Icon/Image div
    div_img = soup.new_tag('div', **{'class': "h-40 bg-fbBlue/10 flex items-center justify-center relative overflow-hidden"})
    div_overlay = soup.new_tag('div', **{'class': "absolute inset-0 bg-gradient-to-t from-black/80 to-transparent z-10"})
    div_img.append(div_overlay)
    
    # Icon
    # Determine icon/color based on category/title keywords if possible
    icon_name = "book-open"
    icon_color = "text-fbBlue"
    if "解封" in post['title'] or "封" in post['title']:
        icon_name = "alert-octagon"
        icon_color = "text-red-500"
    elif "干货" in post['title'] or "运营" in post['title']:
        icon_name = "trending-up"
        icon_color = "text-green-500"
        
    icon = soup.new_tag('i', **{'data-lucide': icon_name, 'class': f"w-12 h-12 {icon_color} opacity-50 group-hover:scale-110 transition-transform duration-500"})
    div_img.append(icon)
    a.append(div_img)
    
    # Content div
    div_content = soup.new_tag('div', **{'class': "p-6"})
    
    # Meta
    # Meta color matches icon color roughly
    meta_color_cls = icon_color.replace("text-", "text-").replace("500", "400") # e.g. text-red-400
    if "fbBlue" in icon_color: meta_color_cls = "text-fbBlue"
    
    div_meta = soup.new_tag('div', **{'class': f"flex items-center gap-2 text-xs font-bold {meta_color_cls} mb-2"})
    span_cat = soup.new_tag('span')
    span_cat.string = post['category']
    span_dot = soup.new_tag('span', **{'class': "text-gray-600"})
    span_dot.string = "•"
    span_date = soup.new_tag('span', **{'class': "text-gray-500"})
    span_date.string = post['date']
    div_meta.append(span_cat)
    div_meta.append(span_dot)
    div_meta.append(span_date)
    div_content.append(div_meta)
    
    # Title
    h3 = soup.new_tag('h3', **{'class': "text-lg font-bold text-white mb-2 line-clamp-2 group-hover:text-fbBlue transition-colors"})
    h3.string = post['title']
    div_content.append(h3)
    
    # Desc
    p_desc = soup.new_tag('p', **{'class': "text-sm text-gray-400 line-clamp-2"})
    p_desc.string = post['description']
    div_content.append(p_desc)
    
    a.append(div_content)
    return a

def build():
    print("Starting build process...")
    
    # 1. Parse index.html
    index_soup = get_soup(INDEX_FILE)
    nav, footer, favicons = extract_nav_footer_favicon(index_soup)
    print("Assets extracted from index.html")
    
    # 2. Process Blog Posts
    posts = []
    for filepath in glob.glob(os.path.join(BLOG_DIR, "*.html")):
        filename = os.path.basename(filepath)
        if filename == "index.html":
            continue
            
        print(f"Processing {filename}...")
        soup = get_soup(filepath)
        post_meta = get_post_metadata(soup, filename)
        post_meta['filepath'] = filepath
        posts.append(post_meta)
    
    # Sort posts by date
    posts.sort(key=lambda x: x['date'], reverse=True)
    
    # 3. Update each blog post
    for post in posts:
        soup = post['soup']
        
        # Phase 2: Head Reconstruction
        reconstruct_head(soup, post, favicons)
        
        # --- Phase 3: Injection ---
        
        # 1. Nav/Footer
        if soup.body:
            new_nav = copy.copy(nav)
            if soup.body.nav: soup.body.nav.replace_with(new_nav)
            else: soup.body.insert(0, new_nav)
            
            new_footer = copy.copy(footer)
            if soup.body.footer: soup.body.footer.replace_with(new_footer)
            else: soup.body.append(new_footer)
            
        # 2. Recommended Reading
        existing_rec = None
        for section in soup.find_all('section'):
            if section.h2 and "推荐阅读" in section.h2.text:
                existing_rec = section
                break
        
        # Create new Rec section
        new_rec = soup.new_tag('section', **{'class': 'py-12 bg-black border-t border-white/5'})
        container = soup.new_tag('div', **{'class': 'max-w-7xl mx-auto px-4 sm:px-6 lg:px-8'})
        new_rec.append(container)
        
        h2 = soup.new_tag('h2', **{'class': 'text-2xl font-bold text-white mb-8 flex items-center gap-2'})
        h2.append(BeautifulSoup('<i data-lucide="book-open" class="w-6 h-6 text-fbBlue"></i> 推荐阅读', 'html.parser'))
        container.append(h2)
        
        grid = soup.new_tag('div', **{'class': 'grid grid-cols-1 md:grid-cols-3 gap-6'})
        container.append(grid)
        
        # Add 3 recs
        count = 0
        for p in posts:
            if p['filename'] == post['filename']: continue
            if count >= 3: break
            grid.append(create_article_card(soup, p))
            count += 1
            
        if existing_rec:
            existing_rec.replace_with(new_rec)
        else:
            # Append before footer
            if soup.body.footer:
                soup.body.footer.insert_before(new_rec)
            else:
                soup.body.append(new_rec)
                
        # Save file
        with open(post['filepath'], 'w', encoding='utf-8') as f:
            f.write(str(soup))
            
    # 4. Process Generic Pages (Root + blog/index.html)
    generic_files = glob.glob(os.path.join(PROJECT_ROOT, "*.html"))
    blog_index = os.path.join(BLOG_DIR, "index.html")
    if os.path.exists(blog_index):
        generic_files.append(blog_index)

    print("Processing generic pages...")
    for filepath in generic_files:
        filename = os.path.basename(filepath)
        is_root_index = (filename == "index.html" and os.path.dirname(filepath) == PROJECT_ROOT)
        if is_root_index: 
            continue # Handle root index last
        if "google" in filename: continue
        
        print(f"Processing {filename}...")
        soup = get_soup(filepath)
        
        # Metadata
        title = soup.title.string if soup.title else filename
        desc = ""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc: desc = meta_desc.get('content', '')
        
        # URL calculation
        rel_path = os.path.relpath(filepath, PROJECT_ROOT)
        url_path = '/' + rel_path.replace('.html', '')
        if url_path.endswith('/index'): url_path = url_path[:-6] # /blog/index -> /blog
        if url_path == '': url_path = '/'

        meta = {
            'title': title,
            'description': desc,
            'url': url_path,
            'date': "2026-01-01",
            'category': 'Page'
        }
        
        reconstruct_head(soup, meta, favicons)
        
        # Inject Nav/Footer
        if soup.body:
            new_nav = copy.copy(nav)
            if soup.body.nav: soup.body.nav.replace_with(new_nav)
            else: soup.body.insert(0, new_nav)
            
            new_footer = copy.copy(footer)
            if soup.body.footer: soup.body.footer.replace_with(new_footer)
            else: soup.body.append(new_footer)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(str(soup))

    # 5. Global Update (Index.html)
    # Update Index Head
    index_meta = {
        'title': index_soup.title.string if index_soup.title else "FBMai",
        'description': index_soup.find('meta', attrs={'name': 'description'})['content'] if index_soup.find('meta', attrs={'name': 'description'}) else "",
        'url': "/",
        'date': "2026-01-01",
        'category': 'Page'
    }
    reconstruct_head(index_soup, index_meta, favicons)

    # Find Latest Articles section
    latest_section = None
    for section in index_soup.find_all('section'):
        if section.h2 and "Latest" in section.h2.text and "Articles" in section.h2.text:
            latest_section = section
            break
            
    if latest_section:
        # Find the grid
        grid = latest_section.find('div', class_=lambda x: x and 'grid' in x and 'md:grid-cols-3' in x)
        if grid:
            grid.clear()
            # Add latest 3 posts
            for p in posts[:3]:
                grid.append(create_article_card(index_soup, p))
                
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(str(index_soup))
        
    print("Build complete.")

if __name__ == "__main__":
    build()
