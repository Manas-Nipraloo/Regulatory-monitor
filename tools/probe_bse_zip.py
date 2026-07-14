import httpx, zipfile
from io import BytesIO
url='https://www.bseindia.com/Download/NocUnder/20260703134137-Website%20Upload.zip'
h={'User-Agent':'Mozilla/5.0','Referer':'https://www.bseindia.com/corporates/NOCUnder_New','Accept':'application/zip,application/octet-stream,*/*'}
r=httpx.get(url,headers=h,follow_redirects=True,timeout=60)
print(r.status_code, r.headers.get('content-type'), len(r.content), r.content[:4])
z=zipfile.ZipFile(BytesIO(r.content))
print(z.namelist())
for info in z.infolist(): print(info.filename, info.file_size)
