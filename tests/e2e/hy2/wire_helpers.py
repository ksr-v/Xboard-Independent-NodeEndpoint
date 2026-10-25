"""Dedicated-namespace echo and UDP header-only observation fixture.

No packet payload, credentials, raw capture or packet bytes are written. The
observer decodes only Ethernet/IP/UDP headers and discards each frame after
updating counters. There is deliberately no logging of exceptions or payload.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import struct
import threading
import time

SERVER_IP = "10.213.25.1"
CLIENT_IP = "10.213.25.2"
INTERFACE = "hys-1025"
PORTS = {42443, 42450, 42451, 42452, 42453}
STOP = threading.Event()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def on_signal(_signum, _frame) -> None:
    STOP.set()


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(0.5)
        while not STOP.is_set():
            try:
                data = self.request.recv(65536)
                if not data:
                    return
                self.request.sendall(data)
            except socket.timeout:
                continue
            except OSError:
                return


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


def echo(ready: Path) -> None:
    tcp = TCPServer((SERVER_IP, 42901), TCPHandler)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind((SERVER_IP, 42902))
    udp.settimeout(0.5)
    thread = threading.Thread(target=tcp.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    write_json(ready, {"ready": True, "mode": "echo", "bind": SERVER_IP, "tcp_port": 42901, "udp_port": 42902})
    try:
        while not STOP.is_set():
            try:
                data, address = udp.recvfrom(65536)
                udp.sendto(data, address)
            except socket.timeout:
                continue
            except OSError:
                if not STOP.is_set():
                    raise
    finally:
        udp.close()
        tcp.shutdown()
        tcp.server_close()
        thread.join(timeout=2)


def udp_header(frame: bytes):
    """Return only verified IPv4/UDP header fields for this private link."""
    if len(frame) < 42 or frame[12:14] != b"\x08\x00":
        return None
    ip_offset = 14
    if frame[ip_offset] >> 4 != 4:
        return None
    ihl = (frame[ip_offset] & 15) * 4
    if ihl < 20 or len(frame) < ip_offset + ihl + 8 or frame[ip_offset + 9] != 17:
        return None
    # Reject fragments: their later bytes cannot reliably be a UDP header.
    fragment = struct.unpack_from("!H", frame, ip_offset + 6)[0]
    if fragment & 0x3FFF:
        return None
    if frame[ip_offset + 12:ip_offset + 16] != socket.inet_aton(CLIENT_IP):
        return None
    if frame[ip_offset + 16:ip_offset + 20] != socket.inet_aton(SERVER_IP):
        return None
    source, destination, udp_bytes, _checksum = struct.unpack_from("!HHHH", frame, ip_offset + ihl)
    if destination not in PORTS or udp_bytes < 8:
        return None
    total_bytes = struct.unpack_from("!H", frame, ip_offset + 2)[0]
    if total_bytes < ihl + udp_bytes:
        return None
    return source, destination, udp_bytes, total_bytes


def observe(ready: Path, output: Path) -> None:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    sock.bind((INTERFACE, 0))
    sock.settimeout(0.25)
    start = time.monotonic()
    packets = 0
    ip_bytes = 0
    udp_bytes = 0
    endpoints: dict[tuple[int, int], dict] = {}
    periods: list[dict] = []
    current = None
    write_json(ready, {"ready": True, "mode": "observe", "interface": INTERFACE, "payload_storage": False})
    try:
        while not STOP.is_set():
            try:
                frame = sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                if STOP.is_set():
                    break
                raise
            header = udp_header(frame)
            # Do not retain frames, even if they failed the explicit filter.
            del frame
            if header is None:
                continue
            source, destination, udp_size, ip_size = header
            at = round(time.monotonic() - start, 6)
            key = (source, destination)
            packets += 1
            ip_bytes += ip_size
            udp_bytes += udp_size
            if key not in endpoints:
                endpoints[key] = {"source_port": source, "destination_port": destination, "first_seconds": at, "last_seconds": at, "packets": 0, "ip_bytes": 0, "udp_bytes": 0}
            item = endpoints[key]
            item["last_seconds"] = at
            item["packets"] += 1
            item["ip_bytes"] += ip_size
            item["udp_bytes"] += udp_size
            if current != key:
                periods.append({"source_port": source, "destination_port": destination, "first_seconds": at, "last_seconds": at, "packets": 0, "ip_bytes": 0, "udp_bytes": 0})
                current = key
            period = periods[-1]
            period["last_seconds"] = at
            period["packets"] += 1
            period["ip_bytes"] += ip_size
            period["udp_bytes"] += udp_size
    finally:
        sock.close()
        # Destination changes count actual consecutive observed destination
        # transitions, not just the number of allocated random hop sockets.
        destination_changes = sum(a["destination_port"] != b["destination_port"] for a, b in zip(periods, periods[1:]))
        write_json(output, {
            "mode": "observe", "interface": INTERFACE, "source_ip": CLIENT_IP,
            "destination_ip": SERVER_IP, "allowed_destination_ports": sorted(PORTS),
            "payload_storage": False, "raw_capture_storage": False,
            "elapsed_seconds": round(time.monotonic() - start, 6),
            "packets": packets, "ip_bytes": ip_bytes, "udp_bytes": udp_bytes,
            "distinct_destination_ports": sorted({key[1] for key in endpoints}),
            "distinct_source_ports": sorted({key[0] for key in endpoints}),
            "destination_changes": destination_changes,
            "source_socket_changes": sum(a["source_port"] != b["source_port"] for a, b in zip(periods, periods[1:])),
            "observed_endpoints": list(endpoints.values()),
            "time_periods": periods,
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    echo_parser = modes.add_parser("echo")
    echo_parser.add_argument("--ready", type=Path, required=True)
    observe_parser = modes.add_parser("observe")
    observe_parser.add_argument("--ready", type=Path, required=True)
    observe_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if args.mode == "echo":
        echo(args.ready)
    else:
        observe(args.ready, args.output)


if __name__ == "__main__":
    main()
