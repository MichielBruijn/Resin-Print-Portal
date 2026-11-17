"""Minimal Elegoo .goo (V3.0) reader/writer (big-endian) + resin mixing file."""
import struct

HDR = [  # (name, fmt)
    ('version', '4s'), ('magic', '8s'), ('software', '32s'), ('sw_version', '24s'),
    ('create_time', '24s'), ('machine_name', '32s'), ('machine_type', '32s'),
    ('resin_profile', '32s'), ('aa_level', 'H'), ('grey_level', 'H'), ('blur_level', 'H'),
    ('small_preview', f'{116*116*2}s'), ('small_delim', '2s'),
    ('big_preview', f'{290*290*2}s'), ('big_delim', '2s'),
    ('layer_count', 'I'), ('res_x', 'H'), ('res_y', 'H'), ('mirror_x', 'B'), ('mirror_y', 'B'),
    ('display_w', 'f'), ('display_h', 'f'), ('machine_z', 'f'), ('layer_height', 'f'),
    ('exposure', 'f'), ('delay_mode', 'B'), ('light_off_delay', 'f'),
    ('b_wait_after_cure', 'f'), ('b_wait_after_lift', 'f'), ('b_wait_before_cure', 'f'),
    ('wait_after_cure', 'f'), ('wait_after_lift', 'f'), ('wait_before_cure', 'f'),
    ('b_exposure', 'f'), ('b_layer_count', 'I'),
    ('b_lift_h', 'f'), ('b_lift_s', 'f'), ('lift_h', 'f'), ('lift_s', 'f'),
    ('b_retract_h', 'f'), ('b_retract_s', 'f'), ('retract_h', 'f'), ('retract_s', 'f'),
    ('b_lift_h2', 'f'), ('b_lift_s2', 'f'), ('lift_h2', 'f'), ('lift_s2', 'f'),
    ('b_retract_h2', 'f'), ('b_retract_s2', 'f'), ('retract_h2', 'f'), ('retract_s2', 'f'),
    ('b_pwm', 'H'), ('pwm', 'H'), ('per_layer', 'B'), ('print_time', 'I'),
    ('volume', 'f'), ('grams', 'f'), ('cost', 'f'), ('price_unit', '8s'),
    ('layer_def_addr', 'I'), ('grayscale', 'B'), ('transition_layers', 'H'),
]
LAYER = [
    ('pause', 'H'), ('pause_z', 'f'), ('z', 'f'), ('exposure', 'f'), ('light_off_delay', 'f'),
    ('wait_after_cure', 'f'), ('wait_after_lift', 'f'), ('wait_before_cure', 'f'),
    ('lift_h', 'f'), ('lift_s', 'f'), ('lift_h2', 'f'), ('lift_s2', 'f'),
    ('retract_h', 'f'), ('retract_s', 'f'), ('retract_h2', 'f'), ('retract_s2', 'f'),
    ('pwm', 'H'), ('delim', '2s'), ('data_len', 'I'),
]
FOOTER = bytes([0, 0, 0, 7, 0, 0, 0, 0x44, 0x4C, 0x50, 0])


def _fmt(spec):
    return '>' + ''.join(f for _, f in spec)


def parse(buf):
    hsize = struct.calcsize(_fmt(HDR))
    hdr = dict(zip([n for n, _ in HDR], struct.unpack_from(_fmt(HDR), buf, 0)))
    assert hdr['version'] == b'V3.0', hdr['version']
    assert hdr['layer_def_addr'] == hsize, (hdr['layer_def_addr'], hsize)
    off = hsize
    layers = []
    lsize = struct.calcsize(_fmt(LAYER))
    for _ in range(hdr['layer_count']):
        lay = dict(zip([n for n, _ in LAYER], struct.unpack_from(_fmt(LAYER), buf, off)))
        off += lsize
        lay['data'] = buf[off:off + lay['data_len']]
        off += lay['data_len']
        assert buf[off:off + 2] == b'\r\n', 'layer delimiter'
        off += 2
        layers.append(lay)
    assert buf[off:] == FOOTER, buf[off:]
    return hdr, layers


def build(hdr, layers):
    hdr = dict(hdr, layer_count=len(layers), layer_def_addr=struct.calcsize(_fmt(HDR)))
    out = bytearray(struct.pack(_fmt(HDR), *[hdr[n] for n, _ in HDR]))
    for lay in layers:
        lay = dict(lay, data_len=len(lay['data']), delim=b'\r\n')
        out += struct.pack(_fmt(LAYER), *[lay[n] for n, _ in LAYER])
        out += lay['data'] + b'\r\n'
    out += FOOTER
    return bytes(out)


def checksum(body):
    return (~sum(body)) & 0xFF


def decode(data, npix):
    """RLE layer data -> bytearray of pixel values."""
    assert data[0] == 0x55, 'layer magic'
    assert checksum(data[1:-1]) == data[-1], 'checksum'
    px = bytearray()
    i, end, cur = 1, len(data) - 1, 0
    while i < end:
        b = data[i]; i += 1
        kind, nlen = b >> 6, (b >> 4) & 3
        if kind == 2:  # diff to previous value, length in low nibble or next byte
            diff = b & 0x0F
            if nlen & 1:
                cnt = data[i]; i += 1
            else:
                cnt = 1
            cur = cur + diff if nlen & 2 == 0 else cur - diff
            px += bytes([cur]) * cnt
            continue
        if kind == 1:
            val = data[i]; i += 1
        else:
            val = 0 if kind == 0 else 0xFF
        cnt = 0  # extra bytes are MSB first, low nibble of b is the least significant
        for _ in range(nlen):
            cnt = (cnt << 8) | data[i]; i += 1
        cnt = (cnt << 4) | (b & 0x0F)
        px += bytes([val]) * cnt
        cur = val
    assert len(px) == npix, (len(px), npix)
    return px


def encode_black(npix):
    """All-black layer: chunks of 0x00 runs (28-bit lengths)."""
    body = bytearray()
    left = npix
    while left:
        n = min(left, (1 << 28) - 1)
        body += bytes([0x30 | (n & 0x0F), (n >> 20) & 0xFF, (n >> 12) & 0xFF, (n >> 4) & 0xFF])
        left -= n
    return bytes([0x55]) + bytes(body) + bytes([checksum(body)])


# --- Resin mixing file -------------------------------------------------------
# Header values for the ELEGOO Mars 4 Ultra (taken from a slicer-made .goo)
MARS4_ULTRA = dict(
    version=b'V3.0', magic=bytes([7, 0, 0, 0, 0x44, 0x4C, 0x50, 0]),
    machine_name=b'ELEGOO Mars 4 Ultra', machine_type=b'ELEGOO Mars 4 Ultra',
    resin_profile=b'Standard Resin_Normal', aa_level=2, grey_level=0, blur_level=2,
    res_x=8520, res_y=4320, mirror_x=1, mirror_y=0,
    display_w=153.36, display_h=77.76, machine_z=165.0, grayscale=1,
)
MIX_LAYER_HEIGHT = 0.01   # bottom position, same for every cycle
MIX_FAST = 500.0          # mm/min
MIX_SLOW = 150.0          # mm/min, first/last mm near the vat floor (FEP)
MIX_SLOW_MM = 1.0


def mix_seconds(cycles, stroke):
    """Rough duration estimate (movement + firmware overhead)."""
    per = 2 * (MIX_SLOW_MM / MIX_SLOW + (stroke - MIX_SLOW_MM) / MIX_FAST) * 60 + 1.6
    return per * cycles


def build_mix(cycles, stroke):
    """.goo that moves the platform floor -> +stroke -> floor, `cycles` times.
    All layers are black and the LED power is 0: nothing is exposed."""
    fast_mm = stroke - MIX_SLOW_MM
    mv = dict(lift_h=MIX_SLOW_MM, lift_s=MIX_SLOW, lift_h2=fast_mm, lift_s2=MIX_FAST,
              retract_h=fast_mm, retract_s=MIX_FAST, retract_h2=MIX_SLOW_MM, retract_s2=MIX_SLOW)
    hdr = dict(MARS4_ULTRA,
               software=b'0.0.11', sw_version=b'0.0.11', create_time=b'0',
               small_preview=bytes(116 * 116 * 2), small_delim=b'\r\n',
               big_preview=bytes(290 * 290 * 2), big_delim=b'\r\n',
               layer_height=MIX_LAYER_HEIGHT, exposure=0.1, b_exposure=0.1,
               delay_mode=1, light_off_delay=0.0,
               b_wait_after_cure=0.0, b_wait_after_lift=0.0, b_wait_before_cure=0.0,
               wait_after_cure=0.0, wait_after_lift=0.0, wait_before_cure=0.0,
               b_layer_count=1, transition_layers=0, b_pwm=0, pwm=0,
               per_layer=1,  # printer must use the per-layer Z (constant)
               print_time=int(mix_seconds(cycles, stroke)),
               volume=0.0, grams=0.0, cost=0.0, price_unit='€/L'.encode())
    for p in ('', 'b_'):
        for k, v in mv.items():
            hdr[p + k] = v
    black = encode_black(hdr['res_x'] * hdr['res_y'])
    z = MIX_LAYER_HEIGHT
    layer = dict(pause=0, pause_z=z, z=z, exposure=0.1, light_off_delay=0.0,
                 wait_after_cure=0.0, wait_after_lift=0.0, wait_before_cure=0.0,
                 pwm=0, data=black, **mv)
    return build(hdr, [layer] * cycles)


# --- Raise the platform when a print is done -------------------------------
RAISE_MIN_MM = 10.0  # don't bother for less than this


def add_raise(buf, target_mm):
    """Slicer .goo + one unexposed last layer that lifts the platform to
    `target_mm`. None if the platform is already that high (tall print).
    Needs per_layer=1 so the printer uses the per-layer Z and lift values;
    slicers already write those equal to the global ones."""
    hdr, layers = parse(buf)
    last = layers[-1]
    target = min(float(target_mm), hdr['machine_z'])
    z = last['z'] + hdr['layer_height']
    dist = target - z
    if dist < RAISE_MIN_MM:
        return None
    slow = min(last['lift_h'], dist)  # peel phase as the slicer set it
    raise_layer = dict(last, pause=0, pause_z=z, z=z, exposure=0.1, light_off_delay=0.0,
                       wait_after_cure=0.0, wait_after_lift=0.0, wait_before_cure=0.0, pwm=0,
                       lift_h=slow, lift_h2=dist - slow, retract_h=0.0, retract_h2=0.0,
                       data=encode_black(hdr['res_x'] * hdr['res_y']))
    secs = slow / last['lift_s'] * 60 + (dist - slow) / last['lift_s2'] * 60
    hdr = dict(hdr, per_layer=1, print_time=hdr['print_time'] + int(secs))
    return build(hdr, layers + [raise_layer])
