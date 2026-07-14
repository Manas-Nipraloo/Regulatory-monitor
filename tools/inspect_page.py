import re
import sys
from urllib.parse import urljoin

import httpx

url = sys.argv[1]
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.bseindia.com/",
}
response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
print(response.status_code, response.url)
text = response.text
print("len", len(text))
for value in re.findall(r'(?:src|href)="([^"]+)"', text):
    print(urljoin(str(response.url), value))
