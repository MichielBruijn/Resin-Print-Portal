"""Find SDCP printers (Elegoo Mars/Saturn) on the network: UDP 'M99999' to port 3000."""
import ipaddress
import json
import select
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, wait

SDCP_PORT = 3000
MAX_SWEEP = 1024  # hosts per subnet for a unicast sweep (/22)


def local_networks():
    """IPv4 networks of the up interfaces (without loopback), via `ip -j addr`."""
    try:
        out = subprocess.run(['ip', '-j', '-4', 'addr', 'show', 'up'],
                             capture_output=True, text=True, timeout=5).stdout
        nets = []
        for iface in json.loads(out or '[]'):
            if iface.get('link_type') == 'loopback':
                continue
            for a in iface.get('addr_info', []):
                if a.get('family') == 'inet' and a.get('prefixlen', 32) < 31:
                    nets.append(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
        return nets
    except Exception:
        return []


def parse_subnets(text):
    """'192.168.1.0/24, 10.0.0.5' -> list of networks; ValueError on nonsense or too big."""
    nets = []
    for part in text.replace(',', ' ').split():
        net = ipaddress.ip_network(part, strict=False)
        if net.version != 4:
            raise ValueError(f'{part}: IPv4 only')
        if net.num_addresses > MAX_SWEEP:
            raise ValueError(f'{part}: too big, at most /22')
        nets.append(net)
    return nets


def hostname(ip):
    """PTR name of ip, only if it resolves back to the same address; else ''."""
    try:
        name = socket.gethostbyaddr(ip)[0]
        return name if ip in socket.gethostbyname_ex(name)[2] else ''
    except OSError:
        return ''


def add_hostnames(printers, timeout=2.0):
    """Fill p['hostname'] in parallel; slow DNS gives up after `timeout`."""
    if not printers:
        return
    pool = ThreadPoolExecutor(max_workers=min(8, len(printers)))
    futures = {pool.submit(hostname, p['ip']): p for p in printers}
    done, _ = wait(futures, timeout)
    for f, p in futures.items():
        p['hostname'] = f.result() if f in done else ''
    pool.shutdown(wait=False)


def discover(subnets=(), timeout=2.0):
    """Broadcast on every local network + unicast to each host in `subnets`.
    Returns [{ip, hostname, name, machine, firmware, id, status}] sorted by IP."""
    targets = {'255.255.255.255'}
    targets.update(str(n.broadcast_address) for n in local_networks())
    for net in subnets:
        hosts = [net.network_address] if net.num_addresses == 1 else net.hosts()
        targets.update(str(h) for h in hosts)

    # Packets to hosts that are still being ARP-resolved occupy the send buffer,
    # so a big sweep would block: spread it over several sockets
    targets = sorted(targets)
    socks = []
    for i in range(0, len(targets), 128):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        socks.append(sock)
        for t in targets[i:i + 128]:
            try:
                sock.sendto(b'M99999', (t, SDCP_PORT))
            except OSError:
                pass  # no route, buffer full: skip

    found = {}
    try:
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select(socks, [], [], left)
            for sock in ready:
                try:
                    data, addr = sock.recvfrom(4096)
                    d = json.loads(data.decode('utf-8'))['Data']
                    attr = d.get('Attributes', {})
                except Exception:
                    continue  # ICMP error or something else answering on port 3000
                found[addr[0]] = {
                    'ip': addr[0],
                    'name': attr.get('Name', ''),
                    'machine': attr.get('MachineName', ''),
                    'firmware': attr.get('FirmwareVersion', ''),
                    'id': attr.get('MainboardID', ''),
                    'status': d.get('Status', {}).get('CurrentStatus'),
                }
    finally:
        for sock in socks:
            sock.close()
    printers = sorted(found.values(), key=lambda p: ipaddress.ip_address(p['ip']))
    add_hostnames(printers)
    return printers


if __name__ == '__main__':
    import sys
    for p in discover(parse_subnets(' '.join(sys.argv[1:]))):
        print(f"{p['ip']:15} {p['hostname'] or '-':25} {p['machine']} ({p['name']}) fw {p['firmware']}")
