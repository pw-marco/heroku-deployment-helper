import re
from urllib.parse import urlparse
from uuid import uuid4
from lxml.etree import HTML
from cloudscraper import create_scraper

class DirectDownloadLinkException(Exception):
    pass

def is_sharer_link(url: str):
    return bool(re.match(r'https?:\/\/.+\.gdtot\.\S+|https?:\/\/(filepress|filebee|appdrive|gdflix)\.\S+', url))

def sharer_scraper(url):
    cget = create_scraper().request
    try:
        url = cget('GET', url).url
        raw = urlparse(url)
        header = {"useragent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10"}
        res = cget('GET', url, headers=header)
    except Exception as e:
        raise DirectDownloadLinkException(f'ERROR: {e}') from e
    
    key = re.findall(r'"key",\s+"(.*?)"', res.text)
    if not key:
        raise DirectDownloadLinkException("ERROR: Key not found!")
    key = key[0]
    
    if not HTML(res.text).xpath("//button[@id='drc']"):
        raise DirectDownloadLinkException("ERROR: This link don't have direct download button.")
        
    boundary = uuid4()
    headers = {
        'Content-Type': f'multipart/form-data; boundary=----WebKitFormBoundary{boundary}',
        'x-token': raw.hostname,
        'useragent': 'Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10'
    }
    data = (
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\ndirect\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="key"\r\n\r\n{key}\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action_token"\r\n\r\n\r\n'
        f'------WebKitFormBoundary{boundary}--\r\n'
    )
    
    try:
        res = cget("POST", url, cookies=res.cookies, headers=headers, data=data).json()
    except Exception as e:
        raise DirectDownloadLinkException(f'ERROR: {e}') from e
        
    if "url" not in res:
        raise DirectDownloadLinkException('ERROR: Drive Link not found, Try in your browser!')
        
    if "drive.google.com" in res["url"]:
        return res["url"]
        
    try:
        res = cget('GET', res["url"])
    except Exception as e:
        raise DirectDownloadLinkException(f'ERROR: {e}') from e
        
    drive_link = HTML(res.text).xpath("//a[contains(@class,'btn')]/@href")
    if drive_link and "drive.google.com" in drive_link[0]:
        return drive_link[0]
        
    raise DirectDownloadLinkException('ERROR: Drive Link not found, Try in your browser!')

def doods(url):
    cget = create_scraper().request
    try:
        res = cget('GET', url)
        # Just a placeholder basic scraper for dood, real one is complex, yt-dlp usually handles dood if valid
        return url 
    except Exception:
        return url

def direct_link_generator(link: str):
    domain = urlparse(link).hostname
    if not domain:
        raise DirectDownloadLinkException("ERROR: Invalid URL")
        
    if is_sharer_link(link):
        return sharer_scraper(link)
        
    if 'dood' in domain:
        return doods(link)
        
    # More bypassers can be added here easily
    return link # Default: return original link if no bypasser matched
