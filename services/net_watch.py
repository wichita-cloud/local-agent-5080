#!/usr/bin/env python3
"""net_watch.py <seconds> <out.json>: record every connection that leaves this PC from inside WSL, for bin/privacy-check.sh.

All WSL distros share one virtual network card (eth0), so this sees the agent, its services, and anything else in WSL.
It records only where traffic goes: the destination address, port and protocol, when it was first seen, and the names
asked of DNS. No contents are read or kept. Docker containers are NOT visible here (Docker Desktop sends their traffic
out through its own Windows program), which is why privacy-check.sh also watches Windows' connection table.

Needs root (a raw socket). Standard library only, so it runs before anything else is installed.
Self-test: `sudo python3 net_watch.py 8 /tmp/t.json` while running `curl -s https://example.com` shows that address and name.
"""
import collections, fcntl, json, socket, struct, sys, time

def main():
    secs, out = float(sys.argv[1]), sys.argv[2]
    probe = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(3)); probe.bind(("eth0", 0))
    me = socket.inet_ntoa(fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", b"eth0"))[20:24]); probe.close()
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(3)); s.settimeout(0.5)   # every interface
    flows, dns, first = collections.Counter(), collections.Counter(), {}
    start = time.time(); end = start + secs
    while time.time() < end:
        try:
            pkt, addr = s.recvfrom(65535)
        except socket.timeout:
            continue
        if len(pkt) < 34 or pkt[12:14] != b"\x08\x00":            # IPv4 only (WSL's NAT network has no IPv6 route out)
            continue
        ihl, proto = (pkt[14] & 15) * 4, pkt[23]
        src, dst = socket.inet_ntoa(pkt[26:30]), socket.inet_ntoa(pkt[30:34])
        l4 = 14 + ihl
        dport = struct.unpack("!H", pkt[l4 + 2:l4 + 4])[0] if proto in (6, 17) else 0
        # DNS: WSL answers names through a relay at 10.255.255.254 (not on eth0), so names are read on any interface
        if proto == 17 and dport == 53 and addr[2] != socket.PACKET_HOST:
            try:
                dns[qname(pkt, l4 + 8 + 12)] += 1
            except Exception:
                pass
            continue
        if src != me or addr[0] != "eth0":
            continue
        if proto == 6 and not (pkt[l4 + 13] & 0x02 and not pkt[l4 + 13] & 0x10):   # TCP: count connection starts (SYN)
            continue
        key = ("tcp" if proto == 6 else "udp" if proto == 17 else "ip-%d" % proto, dst, dport)
        flows[key] += 1
        first.setdefault(key, time.strftime("%H:%M:%S"))
    json.dump({"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)), "seconds": secs, "wsl_address": me,
               "flows": [{"proto": k[0], "dst": k[1], "port": k[2], "packets": v, "first": first[k]} for k, v in flows.most_common()],
               "dns": dict(dns.most_common())}, open(out, "w"), indent=1)

def qname(b, off):
    parts = []
    while off < len(b) and b[off]:
        n = b[off]; parts.append(b[off + 1:off + 1 + n].decode(errors="replace")); off += n + 1
    return ".".join(parts)

if __name__ == "__main__":
    main()
