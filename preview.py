"""Extract the slicer's preview image from .ctb / .goo files as PNG."""
import os
import struct
import zlib

import goo_file

_cache = {}  # path -> (mtime, png bytes or None)


def _png(w, h, rgb):
    raw = b''.join(b'\0' + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def _ctb_rle15(data, w, h):
    """ChituBox preview RLE: 16-bit RGB555 dots, bit 0x20 = repeat count follows."""
    px = bytearray()
    i, n = 0, w * h * 3
    while i + 1 < len(data) and len(px) < n:
        dot = struct.unpack_from('<H', data, i)[0]
        i += 2
        rgb = bytes((((dot >> 11) & 0x1F) << 3, ((dot >> 6) & 0x1F) << 3, (dot & 0x1F) << 3))
        rep = 1
        if dot & 0x20 and i + 1 < len(data):
            run = struct.unpack_from('<H', data, i)[0]
            i += 2
            if run & 0x7000 == 0x3000:
                rep += run & 0xFFF
        px += rgb * rep
    return bytes(px[:n]).ljust(n, b'\0')


def _ctb_preview(path):
    # Preview headers (width, height, data offset, data length) are not encrypted,
    # even in encrypted CTB files; find them near the start and take the largest.
    with open(path, 'rb') as f:
        head = f.read(256 * 1024)
        size = os.fstat(f.fileno()).st_size
        best = None
        for off in range(0, len(head) - 16, 4):
            w, h, io, il = struct.unpack_from('<4I', head, off)
            if (50 <= w <= 2000 and 50 <= h <= 2000 and io in (off + 16, off + 32)
                    and 0 < il <= 8 * w * h and io + il <= size):
                if best is None or w * h > best[0] * best[1]:
                    best = (w, h, io, il)
        if best is None:
            return None
        w, h, io, il = best
        f.seek(io)
        return _png(w, h, _ctb_rle15(f.read(il), w, h))


def _goo_preview(path):
    with open(path, 'rb') as f:
        hdr = f.read(256 * 1024)
    names = [n for n, _ in goo_file.HDR]
    fmt = goo_file._fmt(goo_file.HDR[:names.index('big_preview') + 1])
    big = struct.unpack_from(fmt, hdr, 0)[-1]
    if not any(big):
        return None
    rgb = bytearray()
    for (v,) in struct.iter_unpack('>H', big):  # RGB565, big-endian
        rgb += bytes((((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3))
    return _png(290, 290, bytes(rgb))


def preview_png(path):
    """PNG bytes of the file's preview, or None. Cached per file version."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        ext = path.lower().rsplit('.', 1)[-1]
        png = _ctb_preview(path) if ext == 'ctb' else _goo_preview(path) if ext == 'goo' else None
    except Exception as e:
        print(f"Preview failed for {path}: {e}", flush=True)
        png = None
    _cache[path] = (mtime, png)
    return png


# ChituBox encrypts the settings block of newer CTB files (key/IV as published by UVtools)
_CTB_KEY = bytes.fromhex('D05B8E3371DE3D1AE54F22DDDF5BFD94AB5D643A9D7EBFAF4203F310D8522AEA')
_CTB_IV = bytes.fromhex('0F010A05050B060708060A0C0C0D090F')
_info_cache = {}  # path -> (mtime, info or None)


def _ctb_info(path):
    with open(path, 'rb') as f:
        head = f.read(4096)
        magic = struct.unpack_from('<I', head, 0)[0]
        if magic == 0x12FD0107:  # encrypted
            size, off = struct.unpack_from('<2I', head, 4)
            f.seek(off)
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            dec = Cipher(algorithms.AES(_CTB_KEY), modes.CBC(_CTB_IV)).decryptor()
            s = dec.update(f.read(size)) + dec.finalize()
            lh = struct.unpack_from('<f', s, 0x24)[0]
            count = struct.unpack_from('<I', s, 0x40)[0]
        elif magic in (0x12FD0019, 0x12FD0086):
            lh = struct.unpack_from('<f', head, 0x20)[0]
            count = struct.unpack_from('<I', head, 0x44)[0]
        else:
            return None
    return lh, count


def _goo_info(path):
    fmt = goo_file._fmt(goo_file.HDR)
    with open(path, 'rb') as f:
        hdr = dict(zip([n for n, _ in goo_file.HDR],
                       struct.unpack_from(fmt, f.read(struct.calcsize(fmt)))))
    return hdr['layer_height'], hdr['layer_count']


def file_info(path):
    """{'layers', 'layer_height', 'height'} (mm) from the file header, or None."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _info_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    info = None
    try:
        ext = path.lower().rsplit('.', 1)[-1]
        r = _ctb_info(path) if ext == 'ctb' else _goo_info(path) if ext == 'goo' else None
        if r and 0.001 <= r[0] <= 1 and 0 < r[1] <= 200000:
            lh, count = round(r[0], 4), r[1]
            info = {'layers': count, 'layer_height': lh, 'height': round(lh * count, 2)}
    except Exception as e:
        print(f"File info failed for {path}: {e}", flush=True)
    _info_cache[path] = (mtime, info)
    return info
