"""Minimal MQTT 3.1.1 client (QoS 0): one-shot publish, or a persistent Client that also receives."""
import os
import select
import socket
import struct
import time

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


class Client:
    """Persistent connection: publish, subscribe and poll() for incoming messages."""

    def __init__(self, host, port, username='', password='', will=None, keepalive=60, timeout=10):
        self.host, self.port, self.timeout, self.keepalive = host, int(port), timeout, keepalive
        self.username, self.password, self.will = username, password, will  # will: (topic, payload, retain)
        self.sock = None
        self._pid = 0

    def connect(self):
        flags, tail = 0x02, _str('rpp-' + os.urandom(4).hex())  # clean session
        if self.will:
            topic, payload, retain = self.will
            flags |= 0x04 | (0x20 if retain else 0)
            tail += _str(topic) + _str(payload)
        if self.username:
            flags |= 0x80
            tail += _str(self.username)
            if self.password:
                flags |= 0x40
                tail += _str(self.password)
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            self._send(0x10, _str('MQTT') + bytes([4, flags]) + struct.pack('>H', self.keepalive) + tail)
            ack = _recv(self.sock, 4)
            if ack[0] != 0x20:
                raise ConnectionError('no CONNACK from broker')
            if ack[3]:
                raise ConnectionError(_CONNACK.get(ack[3], f'connection refused ({ack[3]})'))
        except Exception:
            self.close(polite=False)
            raise
        self.last_rx = time.monotonic()
        self.ping_sent = None
        return self

    def _send(self, kind, body):
        self.sock.sendall(_packet(kind, body))
        self.last_tx = time.monotonic()

    def publish(self, topic, payload, retain=False):
        if isinstance(payload, str):
            payload = payload.encode()
        self._send(0x31 if retain else 0x30, _str(topic) + payload)

    def subscribe(self, topic):
        self._pid = self._pid % 65535 + 1
        self._send(0x82, struct.pack('>H', self._pid) + _str(topic) + b'\x00')

    def _read_packet(self):
        head = _recv(self.sock, 1)[0]
        n, mult = 0, 1
        while True:
            b = _recv(self.sock, 1)[0]
            n += (b & 0x7f) * mult
            mult *= 128
            if not b & 0x80:
                break
        return head, _recv(self.sock, n) if n else b''

    def poll(self, timeout):
        """Wait up to timeout s; returns [(topic, payload bytes)] and keeps the connection alive."""
        msgs, end = [], time.monotonic() + timeout
        while True:
            now = time.monotonic()
            if self.ping_sent and now - self.ping_sent > self.keepalive:
                raise ConnectionError('broker stopped answering')
            # Ping on silence from the broker: only an answer proves the link is alive
            if not self.ping_sent and now - self.last_rx > self.keepalive / 2:
                self._send(0xc0, b'')  # PINGREQ
                self.ping_sent = now
            wait = min(end - now, self.keepalive / 4)
            if wait <= 0:
                return msgs
            if not select.select([self.sock], [], [], wait)[0]:
                continue
            head, body = self._read_packet()
            self.last_rx = time.monotonic()
            self.ping_sent = None  # anything from the broker proves it is alive
            if head >> 4 == 3:  # PUBLISH
                tlen = struct.unpack('>H', body[:2])[0]
                topic, rest = body[2:2 + tlen].decode('utf-8', 'replace'), body[2 + tlen:]
                qos = (head >> 1) & 3
                if qos:
                    pid, rest = rest[:2], rest[2:]
                    if qos == 1:
                        self._send(0x40, pid)  # PUBACK
                msgs.append((topic, rest))
                end = min(end, time.monotonic() + 0.2)  # return soon: something to handle

    def close(self, polite=True):
        if self.sock:
            try:
                if polite:
                    self.sock.sendall(b'\xe0\x00')  # DISCONNECT
            except OSError:
                pass
            self.sock.close()
            self.sock = None


def publish(host, port, topic, payload, username='', password='', retain=False, timeout=10):
    """payload None: only connect (checks broker and login)."""
    c = Client(host, port, username, password, timeout=timeout).connect()
    try:
        if payload is not None:
            c.publish(topic, payload, retain)
    finally:
        c.close()
