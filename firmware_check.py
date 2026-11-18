"""Latest printer firmware from Elegoo's download center (the page links every firmware zip)."""
import html
import re
import threading
import time
import urllib.parse
import urllib.request

PAGE = 'https://wiki.elegoo.com/en/download-center'
CHECK_EVERY, RETRY_AFTER = 12 * 3600, 3600

_lock = threading.Lock()
_cache = {'at': 0, 'ok': False, 'links': []}


def _fetch():
    req = urllib.request.Request(PAGE, headers={'User-Agent': 'Resin-Print-Portal'})
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode('utf-8', 'replace')
    return [html.unescape(h) for h in re.findall(r'href="([^"]+\.zip)"', page)]


def _links():
    with _lock:
        age = time.time() - _cache['at']
        if age > (CHECK_EVERY if _cache['ok'] else RETRY_AFTER):
            try:
                _cache.update(links=_fetch(), ok=True)
            except Exception as e:
                print(f"Firmware check failed: {e}", flush=True)
                _cache['ok'] = False  # keep the last list, try again sooner
            _cache['at'] = time.time()
        return _cache['links']


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or ''))


def latest(machine_name):
    """(version 'V1.3.0', zip url) of the newest firmware for e.g. 'ELEGOO Mars 4 Ultra', or (None, None)."""
    model = re.sub(r'^ELEGOO\s+', '', machine_name or '', flags=re.I).strip()
    if not model:
        return None, None
    # File names like 'Mars 4 Ultra-V1.3.0-F42.21-20260605-V6.zip', 'MARS 4 MAX-V4.2.18-...',
    # 'Mars 5 -V2.2.29-...'; the model must be followed by the version, so 'Mars 4' doesn't match 'Mars 4 Ultra'
    pat = re.compile(r'^(?:\d+-)?(?:ELEGOO\s+)?' + r'\s+'.join(map(re.escape, model.split()))
                     + r'(?:\s+Firmware)?\s*-\s*V(\d+(?:\.\d+)+)', re.I)
    best = None
    for url in _links():
        name = urllib.parse.unquote(url.rsplit('/', 1)[-1])
        m = pat.match(name)
        if m and (best is None or version_tuple(m.group(1)) > version_tuple(best[0])):
            best = (m.group(1), url)
    return ('V' + best[0], best[1]) if best else (None, None)


if __name__ == '__main__':
    import sys
    for name in sys.argv[1:] or ['ELEGOO Mars 4 Ultra']:
        print(name, '->', *latest(name))
