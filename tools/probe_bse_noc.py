import json
import httpx

url = 'https://api.bseindia.com/BseIndiaAPI/api/GetNocUnder_ng/w?flag=2&ID=&exchId=&Company_Name=&dt_tm=20260703'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://www.bseindia.com',
    'Referer': 'https://www.bseindia.com/corporates/NOCUnder_New',
}
r = httpx.get(url, headers=headers, timeout=30)
print(r.status_code, r.headers.get('content-type'))
print(r.text[:3000])
try:
    data = r.json()
    print('keys', data.keys())
    print('rows', len(data.get('Table', [])))
    print(json.dumps(data.get('Table', [])[:3], indent=2)[:3000])
except Exception as exc:
    print('json error', repr(exc))
