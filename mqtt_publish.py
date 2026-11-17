"""Minimal MQTT 3.1.1 publisher (QoS 0): connect, publish, disconnect."""
import os
import socket
import struct

_CONNACK = {1: 'unsupported protocol version', 2: 'client id rejected', 3: 'broker unavailable',
            4: 'bad username or password', 5: 'not authorized'}


def _str(s):
    b = s.encode()
    return struct.pack('>H', len(b)) + b


def _packet(kind, body):
    n, rl = len(body), bytearray()
    while True:
        n, d = divmod(n, 128)
        rl.append(d | (0x80 if n else 0))
        if not n:
            break
    return bytes([kind]) + bytes(rl) + body


def _recv(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('broker closed the connection')
        buf += chunk
    return buf


def publish(host, port, topic, payload, username='', password='', retain=False, timeout=10):
    """payload None: only connect (checks broker and login)."""
    flags, tail = 0x02, _str('rpp-' + os.urandom(4).hex())  # clean session
    if username:
        flags |= 0x80
        tail += _str(username)
        if password:
            flags |= 0x40
            tail += _str(password)
    if isinstance(payload, str):
        payload = payload.encode()
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(_packet(0x10, _str('MQTT') + bytes([4, flags]) + struct.pack('>H', 30) + tail))
        ack = _recv(s, 4)
        if ack[0] != 0x20:
            raise ConnectionError('no CONNACK from broker')
        if ack[3]:
            raise ConnectionError(_CONNACK.get(ack[3], f'connection refused ({ack[3]})'))
        if payload is not None:
            s.sendall(_packet(0x31 if retain else 0x30, _str(topic) + payload))
        s.sendall(b'\xe0\x00')  # DISCONNECT
