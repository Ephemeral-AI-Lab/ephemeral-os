"""Send one JSON envelope to the resident sandbox daemon."""

from __future__ import annotations

import os
import socket
import sys

CONNECT_FAILED = 97
IO_FAILED = 98


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: thin_client.py <socket-path> <json-envelope>\n")
        return 2

    socket_path = argv[1]
    payload = argv[2]
    timeout = float(os.environ.get("EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT", "600"))
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        try:
            client.connect(socket_path)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            sys.stderr.write(
                f"EOS_DAEMON_CONNECT_FAILED:{exc.__class__.__name__}\n"
            )
            return CONNECT_FAILED

        try:
            client.sendall(payload.encode("utf-8") + b"\n")
            client.shutdown(socket.SHUT_WR)
        except OSError as exc:
            sys.stderr.write(f"EOS_DAEMON_IO_FAILED:{exc.__class__.__name__}\n")
            return IO_FAILED

        chunks: list[bytes] = []
        try:
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            sys.stderr.write("EOS_DAEMON_IO_FAILED:socket.timeout\n")
            return IO_FAILED
        except OSError as exc:
            sys.stderr.write(f"EOS_DAEMON_IO_FAILED:{exc.__class__.__name__}\n")
            return IO_FAILED

        sys.stdout.buffer.write(b"".join(chunks))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
