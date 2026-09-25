#!/usr/bin/env python3
"""Write the gRPC request bodies and the expected responses to apps/grpc/."""

from pathlib import Path

TEXT = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01"
REPLY = f"Hello from worker, {TEXT}!"
GRPC_DIR = Path(__file__).resolve().parent


def message(text):
    # Tag 0x0a is field 1 with wire type 2 (length-delimited). The texts are shorter than
    # 128 bytes, so the length is a one-byte varint.
    data = text.encode()
    return bytes([0x0A, len(data)]) + data


def frame(flag, payload):
    # Length-prefixed message: a flag byte and a 4-byte big-endian length.
    return bytes([flag]) + len(payload).to_bytes(4, "big") + payload


def main():
    request = message(TEXT)
    reply = message(REPLY)
    grpc_reply = frame(0x00, reply)
    files = {
        "echo.bin": (request, 66),
        "echo.grpc": (frame(0x00, request), 71),
        "echo.json": (f'{{"text":"{TEXT}"}}'.encode(), 75),
        "expect.bin": (reply, 86),
        "expect.grpc": (grpc_reply, 91),
        "expect.grpcweb": (grpc_reply + frame(0x80, b"grpc-status: 0\r\n"), 112),
        "expect.json": (f'{{"text":"{REPLY}"}}'.encode(), 95),
    }
    for name, (data, size) in files.items():
        assert len(data) == size, f"{name} is {len(data)} bytes, expected {size}"
        (GRPC_DIR / name).write_bytes(data)


if __name__ == "__main__":
    main()
