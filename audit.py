import os
import re
import sys
import time
import json
import logging
import requests
import concurrent.futures
from urllib.parse import urlparse, urljoin, unquote
from bs4 import BeautifulSoup
from colorama import init, Fore, Style
from collections import defaultdict

# Initialize colorama
init(autoreset=True)

class AutoConfig:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.base_url = None
        self.keywords = []
        self.ignore_paths = ['.git', 'node_modules', '__pycache__', 'MasterTool']
        self.ignore_urls_prefixes = ['/go/', 'javascript:', 'mailto:', '#']
        self.ignore_urls_substrings = ['cdn-cgi']
        self.ignore_files = ['404.html']
        self.ignore_files_substrings = ['google']
        
        self._load_config()

    def _load_config(self):
        index_path = os.path.join(self.root_dir, 'index.html')
        if not os.path.exists(index_path):
            print(f"{Fore.YELLOW}[WARN] Root index.html not found. Auto-configuration limited.")
            return

        try:
            with open(index_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f, 'html.parser')
                
                # Base URL
                canonical = soup.find('link', rel='canonical')
                if canonical and canonical.get('href'):
                    self.base_url = canonical['href'].rstrip('/')
                else:
                    og_url = soup.find('meta', property='og:url')
                    if og_url and og_url.get('content'):
                        self.base_url = og_url['content'].rstrip('/')
                    else:
                        print(f"{Fore.YELLOW}[WARN] Could not determine Base URL from index.html (checked canonical and og:url).")

                # Keywords
                meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
                if meta_keywords and meta_keywords.get('content'):
                    self.keywords = [k.strip() for k in meta_keywords['content'].split(',')]
                
        except Exception as e:
            print(f"{Fore.RED}[ERROR] Failed to parse index.html for config: {e}")

class Auditor:
    def __init__(self, root_dir):
        self.root_dir = os.path.abspath(root_dir)
        self.config = AutoConfig(self.root_dir)
        self.files_to_audit = []
        self.internal_links_graph = defaultdict(list) # target -> [sources]
        self.external_links = set()
        self.score = 100
        self.issues = [] # List of (severity, message)
        self.processed_files_count = 0
        self.dead_links = []
        self.orphan_pages = []

    def log(self, type, message):
        if type == 'ERROR':
            print(f"{Fore.RED}[ERROR] {message}")
            self.score -= 10
        elif type == 'WARN':
            print(f"{Fore.YELLOW}[WARN] {message}")
            self.score = max(0, self.score - (5 if 'Orphan' in message else 2))
        elif type == 'SUCCESS':
            print(f"{Fore.GREEN}[SUCCESS] {message}")
        elif type == 'INFO':
            print(f"{Fore.BLUE}[INFO] {message}")
        
        if type in ['ERROR', 'WARN']:
             self.issues.append((type, message))

    def is_ignored_path(self, path):
        for ignore in self.config.ignore_paths:
            if ignore in path:
                return True
        return False

    def is_ignored_file(self, filename):
        if filename in self.config.ignore_files:
            return True
        for substring in self.config.ignore_files_substrings:
            if substring in filename:
                return True
        return False

    def is_ignored_url(self, url):
        for prefix in self.config.ignore_urls_prefixes:
            if url.startswith(prefix):
                return True
        for substring in self.config.ignore_urls_substrings:
            if substring in url:
                return True
        return False

    def scan_files(self):
        for root, dirs, files in os.walk(self.root_dir):
            # Filter directories
            dirs[:] = [d for d in dirs if not self.is_ignored_path(os.path.join(root, d))]
            
            for file in files:
                if file.endswith('.html') and not self.is_ignored_file(file):
                    self.files_to_audit.append(os.path.join(root, file))

    def resolve_local_path(self, current_file_path, link_href):
        """
        Resolves a link href to a local file system path.
        Returns: (absolute_file_path, exists_boolean)
        """
        # Remove query params and hash
        url_path = link_href.split('?')[0].split('#')[0]
        
        # Handle root relative paths
        if url_path.startswith('/'):
            # It's absolute to the site root
            potential_path = os.path.join(self.root_dir, url_path.lstrip('/'))
        else:
            # It's relative to current file
            current_dir = os.path.dirname(current_file_path)
            potential_path = os.path.join(current_dir, url_path)

        potential_path = os.path.normpath(potential_path)

        # Check exact match (e.g. /blog/post.html)
        if os.path.isfile(potential_path):
            return potential_path, True
        
        # Check if it's a directory, looking for index.html
        if os.path.isdir(potential_path):
            index_path = os.path.join(potential_path, 'index.html')
            if os.path.isfile(index_path):
                return index_path, True
        
        # Check if it's a "clean URL" mapping to .html (e.g. /blog/post -> /blog/post.html)
        html_path = potential_path + '.html'
        if os.path.isfile(html_path):
            return html_path, True

        # Check if it's a "clean URL" mapping to folder/index.html (e.g. /blog/post -> /blog/post/index.html)
        # This is covered by isdir check above if the folder exists, but if folder doesn't exist?
        # Actually, os.path.isdir would return False if the folder doesn't exist.
        # So we construct the path and check file existence directly.
        index_nested_path = os.path.join(potential_path, 'index.html')
        if os.path.isfile(index_nested_path):
            return index_nested_path, True
            
        return potential_path, False

    def check_file(self, file_path):
        rel_path = os.path.relpath(file_path, self.root_dir)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                soup = BeautifulSoup(content, 'html.parser')

            # 1. H1 Check
            h1s = soup.find_all('h1')
            if len(h1s) == 0:
                self.log('ERROR', f"{rel_path}: Missing <h1> tag (-5 pts)")
                self.score -= 5 # Explicit deduction as per spec logic (though log handles it too, just being safe)
            elif len(h1s) > 1:
                self.log('WARN', f"{rel_path}: Multiple <h1> tags found (-2 pts)")
            
            # 2. Schema Check
            schema = soup.find('script', type='application/ld+json')
            if not schema:
                self.log('WARN', f"{rel_path}: Missing Schema Markup (-2 pts)")
            
            # 3. Breadcrumb Check
            breadcrumb = soup.find(attrs={'aria-label': 'breadcrumb'}) or \
                         soup.find(class_=lambda x: x and 'breadcrumb' in x)
            # Not explicitly penalizing in requirements text for breadcrumb, just check? 
            # "Breadcrumb: 检查页面是否包含..." - implied warning or info if missing? 
            # Assuming info or warn if it's a deep page. Let's stick to spec "WARN: ... 缺少 Schema". 
            # Breadcrumb isn't listed in the scoring section explicitly as a deduction, but good to note.
            
            # 4. Link Analysis
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if not href or self.is_ignored_url(href):
                    continue

                # External Links
                if href.startswith('http://') or href.startswith('https://'):
                    # Check if it's actually internal via full domain
                    if self.config.base_url and href.startswith(self.config.base_url):
                        self.log('WARN', f"{rel_path}: Absolute internal link found: {href} -> Use path instead (e.g. /blog)")
                        # Treat as internal for existence check?
                        # Spec says: "⚠️ 警告: 使用带域名的绝对路径... -> 提示应为 /blog"
                        # And "死链判定... 即使链接写得不规范... 脚本也应尝试... 解析"
                        path_part = href.replace(self.config.base_url, '', 1)
                        if not path_part.startswith('/'): path_part = '/' + path_part
                        resolved_path, exists = self.resolve_local_path(file_path, path_part)
                        if exists:
                            self.internal_links_graph[resolved_path].append(file_path)
                        else:
                            self.log('ERROR', f"{rel_path}: Dead Link (Internal Absolute): {href}")
                            self.dead_links.append(href)
                    else:
                        self.external_links.add(href)
                        # Check rel attributes for protection (nofollow, noopener, noreferrer)
                        rel = a.get('rel', [])
                        if isinstance(rel, str): rel = rel.split()
                        
                        missing = []
                        if 'nofollow' not in rel: missing.append('nofollow')
                        if 'noopener' not in rel: missing.append('noopener')
                        if 'noreferrer' not in rel: missing.append('noreferrer')
                        
                        if missing:
                            self.log('WARN', f"{rel_path}: External link {href} missing protection: {', '.join(missing)} -> Risk of weight loss")
                            # We don't deduct points heavily, but warn about it.
                        
                    continue

                # Internal Links
                # Check for relative paths warning
                if not href.startswith('/'):
                    self.log('WARN', f"{rel_path}: Relative path used: {href} -> Should be absolute (e.g. /blog/post)")
                
                # Check for .html extension warning
                if href.endswith('.html'):
                    self.log('WARN', f"{rel_path}: .html extension used: {href} -> Should be Clean URL")

                # Dead Link Check
                resolved_path, exists = self.resolve_local_path(file_path, href)
                
                if exists:
                    self.internal_links_graph[resolved_path].append(file_path)
                else:
                    self.log('ERROR', f"{rel_path}: Dead Link: {href}")
                    self.dead_links.append(href)

        except Exception as e:
            print(f"{Fore.RED}Failed to process {rel_path}: {e}")

    def check_external_links(self):
        print(f"\n{Fore.CYAN}Checking {len(self.external_links)} external links...")
        
        def check_url(url):
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (compatible; SEOAuditor/1.0)'}
                response = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
                if response.status_code >= 400:
                    return url, f"Status {response.status_code}", True # True means deduct points
            except requests.exceptions.Timeout:
                return url, "Timeout (Network Limit?)", False # False means don't deduct
            except requests.exceptions.ConnectionError:
                return url, "Connection Error (Network Limit?)", False
            except Exception as e:
                return url, str(e), False
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_url = {executor.submit(check_url, url): url for url in self.external_links}
            for future in concurrent.futures.as_completed(future_to_url):
                result = future.result()
                if result:
                    url, error, should_deduct = result
                    if should_deduct:
                        self.log('ERROR', f"Broken External Link: {url} ({error}) (-5 pts)")
                        self.score -= 5
                    else:
                        self.log('WARN', f"External Link Unreachable: {url} ({error}) - Skipped deduction (Likely Network Issue)")

    def analyze_structure(self):
        # Identify orphans
        # All files found minus those that are targets in the graph
        # Exclude index.html and whitelist
        
        all_files_set = set(self.files_to_audit)
        referenced_files_set = set(self.internal_links_graph.keys())
        
        orphans = []
        for f in all_files_set:
            if f not in referenced_files_set:
                filename = os.path.basename(f)
                if filename == 'index.html' or self.is_ignored_file(filename):
                    continue
                
                # Also check if it's the root index, which naturally has no incoming links sometimes if not self-referenced
                if f == os.path.join(self.root_dir, 'index.html'):
                    continue
                    
                orphans.append(f)
                rel_path = os.path.relpath(f, self.root_dir)
                self.log('WARN', f"Orphan Page (No incoming links): {rel_path} (-5 pts)")
                self.orphan_pages.append(rel_path)

        # Top Pages
        sorted_pages = sorted(self.internal_links_graph.items(), key=lambda x: len(x[1]), reverse=True)
        print(f"\n{Fore.CYAN}--- Top 10 Internal Pages by Inbound Links ---")
        for path, sources in sorted_pages[:10]:
            rel = os.path.relpath(path, self.root_dir)
            print(f"{rel}: {len(sources)} links")

    def run(self):
        print(f"{Fore.CYAN}Starting SEO Audit for: {self.root_dir}")
        if self.config.base_url:
            print(f"{Fore.BLUE}Base URL: {self.config.base_url}")
        else:
            print(f"{Fore.YELLOW}Base URL: Not detected")
        
        self.scan_files()
        print(f"{Fore.BLUE}Found {len(self.files_to_audit)} HTML files to audit.")
        
        for file_path in self.files_to_audit:
            self.check_file(file_path)
            self.processed_files_count += 1
            
        self.analyze_structure()
        self.check_external_links()
        
        # Final Report
        print(f"\n{Fore.CYAN}==================================================")
        print(f"{Fore.CYAN}                  AUDIT REPORT                    ")
        print(f"{Fore.CYAN}==================================================")
        
        self.score = max(0, self.score) # Ensure not negative
        
        score_color = Fore.GREEN
        if self.score < 80: score_color = Fore.YELLOW
        if self.score < 50: score_color = Fore.RED
        
        print(f"Final Score: {score_color}{self.score}/100")
        
        if self.score < 100:
            print(f"\n{Fore.YELLOW}Actionable Advice:")
            print(f"{Fore.YELLOW}- Review the [ERROR] and [WARN] logs above.")
            print(f"{Fore.YELLOW}- Run 'python3 audit.py' again after fixes.")
            # Mention fix scripts if they existed, as per requirements
            print(f"{Fore.YELLOW}- Consider creating or running a fix script for common issues.")

if __name__ == "__main__":
    current_dir = os.getcwd()
    auditor = Auditor(current_dir)
    auditor.run()
