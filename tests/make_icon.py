"""Draw icon.png: the sign </> on a rounded teal square (standard library only).

Run from the repository root: ``python tests/make_icon.py``. The output is deterministic.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 128
SAMPLES = 4
BACKGROUND = (15, 118, 110)
FOREGROUND = (255, 255, 255)
RADIUS = 26.0
STROKE = 9.0

#: The sign as line segments: <, /, >.
SEGMENTS = (
    ((46.0, 40.0), (22.0, 64.0)),
    ((22.0, 64.0), (46.0, 88.0)),
    ((74.0, 34.0), (56.0, 94.0)),
    ((82.0, 40.0), (106.0, 64.0)),
    ((106.0, 64.0), (82.0, 88.0)),
)


def _inside_square(x: float, y: float) -> bool:
    cx = min(max(x, RADIUS), SIZE - RADIUS)
    cy = min(max(y, RADIUS), SIZE - RADIUS)
    return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS**2


def _distance(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _on_sign(x: float, y: float) -> bool:
    return any(_distance(x, y, a, b) <= STROKE / 2 for a, b in SEGMENTS)


def pixels() -> bytes:
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            square = sign = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    px = x + (sx + 0.5) / SAMPLES
                    py = y + (sy + 0.5) / SAMPLES
                    if _inside_square(px, py):
                        square += 1
                        if _on_sign(px, py):
                            sign += 1
            total = SAMPLES * SAMPLES
            alpha = round(255 * square / total)
            mix = sign / square if square else 0.0
            colour = [round(b + (f - b) * mix) for b, f in zip(BACKGROUND, FOREGROUND, strict=True)]
            rows.extend((*colour, alpha))
    return bytes(rows)


def png(data: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(data, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    target = Path(__file__).resolve().parents[1] / "icon.png"
    target.write_bytes(png(pixels()))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
