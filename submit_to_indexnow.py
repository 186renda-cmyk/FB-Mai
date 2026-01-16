import urllib.request
import json
import xml.etree.ElementTree as ET
import os

# Configuration
HOST = "fb-mai.top"
KEY = "d18753b123184422bd671c0d6263beff"
KEY_LOCATION = f"https://{HOST}/{KEY}.txt"
SITEMAP_FILE = "sitemap.xml"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"

def get_urls_from_sitemap(sitemap_path):
    """Parses sitemap.xml to extract URLs."""
    urls = []
    try:
        tree = ET.parse(sitemap_path)
        root = tree.getroot()
        # Namespace map usually needed for sitemaps
        namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        
        for url in root.findall('ns:url', namespaces):
            loc = url.find('ns:loc', namespaces)
            if loc is not None and loc.text:
                urls.append(loc.text)
                
    except Exception as e:
        print(f"Error reading sitemap: {e}")
        # Fallback if namespace parsing fails or structure is different
        try:
            tree = ET.parse(sitemap_path)
            root = tree.getroot()
            for elem in root.iter():
                if elem.tag.endswith('loc') and elem.text:
                    urls.append(elem.text)
        except Exception as e2:
             print(f"Error reading sitemap (fallback): {e2}")
             
    # Remove duplicates
    return list(set(urls))

def submit_urls(urls):
    """Submits URLs to IndexNow."""
    if not urls:
        print("No URLs found to submit.")
        return

    data = {
        "host": HOST,
        "key": KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": urls
    }

    json_data = json.dumps(data).encode('utf-8')
    
    req = urllib.request.Request(
        INDEXNOW_ENDPOINT, 
        data=json_data, 
        headers={'Content-Type': 'application/json; charset=utf-8'}
    )

    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"Successfully submitted {len(urls)} URLs to IndexNow.")
                print("Response:", response.read().decode('utf-8'))
            else:
                print(f"Failed to submit. Status code: {response.status}")
                print("Response:", response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        print(e.read().decode('utf-8'))
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sitemap_path = os.path.join(current_dir, SITEMAP_FILE)
    
    if os.path.exists(sitemap_path):
        print(f"Reading URLs from {sitemap_path}...")
        urls = get_urls_from_sitemap(sitemap_path)
        print(f"Found {len(urls)} URLs.")
        submit_urls(urls)
    else:
        print(f"Sitemap file not found at {sitemap_path}")
