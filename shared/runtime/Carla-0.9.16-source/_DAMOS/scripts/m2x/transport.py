"""Length-bounded TCP transport. Authentication is performed by ImageVerifier."""
import queue
import socket
import struct
import threading

from .security import MAX_HEADER, MAX_IMAGE, MAX_NODES, encode_header, decode_header


def recv_exact(sock, length):
    parts = bytearray()
    while len(parts) < length:
        chunk = sock.recv(min(length - len(parts), 65536))
        if not chunk:
            raise ConnectionError("TRUNCATED_PACKET")
        parts.extend(chunk)
    return bytes(parts)


class Inbox:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(MAX_NODES)
        self.sock.settimeout(0.2)
        self.address = self.sock.getsockname()
        self.items = queue.Queue(maxsize=MAX_NODES * 2)
        self.errors = 0
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                with conn:
                    conn.settimeout(2.0)
                    header_size, image_size = struct.unpack("!II", recv_exact(conn, 8))
                    if not 0 < header_size <= MAX_HEADER or not 0 < image_size <= MAX_IMAGE:
                        raise ValueError("PACKET_TOO_LARGE")
                    packet = decode_header(recv_exact(conn, header_size))
                    image = recv_exact(conn, image_size)
                    self.items.put_nowait((packet, image))
            except (OSError, ValueError, queue.Full):
                self.errors += 1

    def close(self):
        self.stopped.set()
        self.sock.close()
        self.thread.join(timeout=3.0)


def send_image(address, packet, image, timeout):
    header = encode_header(packet)
    if not 0 < len(image) <= MAX_IMAGE:
        raise ValueError("IMAGE_TOO_LARGE")
    with socket.create_connection(address, timeout=max(0.001, timeout)) as conn:
        conn.sendall(struct.pack("!II", len(header), len(image)) + header + image)
