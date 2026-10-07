"""PoC 本機 CONNECT 閘道：限制 host、固定公開 IP，TLS 仍由 Chromium 核對。

這是本機出口限制，不使用外部或輪換 proxy。
"""
import ipaddress
import select
import socket
import socketserver
import time

MAX_TUNNEL_BYTES = 8 * 1024 * 1024


def target(line, pins):
    method, authority, version = line.decode('ascii').strip().split(' ')
    if method != 'CONNECT' or version not in ('HTTP/1.0', 'HTTP/1.1'):
        raise ValueError('CONNECT_REQUIRED')
    host, port = authority.rsplit(':', 1)
    if port != '443' or host not in pins:
        raise ValueError('TARGET_REJECTED')
    address = pins[host]
    if not ipaddress.ip_address(address).is_global:
        raise ValueError('NONPUBLIC_IP')
    return address


class Gate(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, pins):
        self.pins = pins
        super().__init__(('127.0.0.1', 0), Tunnel)


class Tunnel(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            address = target(self.rfile.readline(4096), self.server.pins)
            header_bytes = 0
            while True:
                line = self.rfile.readline(4096)
                header_bytes += len(line)
                if not line or header_bytes > 16384:
                    raise ValueError('HEADER_LIMIT')
                if line == b'\r\n': break
        except (ValueError, UnicodeError, OSError):
            self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
            return
        try:
            with socket.create_connection((address, 443), timeout=15) as upstream:
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                self.wfile.flush()
                deadline = time.monotonic() + 30
                transferred = 0
                sockets = (self.connection, upstream)
                while transferred < MAX_TUNNEL_BYTES and time.monotonic() < deadline:
                    readable, _, _ = select.select(sockets, [], [], min(1, max(0, deadline-time.monotonic())))
                    for source in readable:
                        data = source.recv(min(65536, MAX_TUNNEL_BYTES-transferred))
                        if not data: return
                        transferred += len(data)
                        (upstream if source is self.connection else self.connection).sendall(data)
        except OSError:
            return
