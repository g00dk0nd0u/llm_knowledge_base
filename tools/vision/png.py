"""Bounded stdlib PNG pixel-stream checks at the execution boundary."""
import struct
import zlib
from .contract import _png_dimensions, _require


def verify_png(data):
    """Validate chunk integrity plus decompression/scanline lengths and filters.

    Includes Adam7 scanline sizes. Does not reinterpret the image or alter bytes.
    These execution resource limits do not change the existing request schemas.
    """
    _require(len(data) <= 64 * 1024 * 1024, 'PNG exceeds execution byte limit')
    width, height = _png_dimensions(data)
    _require(width * height <= 64_000_000, 'PNG exceeds execution pixel limit')
    _, _, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', data[16:29])
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if not interlace else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    rows = []
    for x, y, dx, dy in passes:
        columns, count = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and count:
            rows.append(((columns * channels * depth + 7) // 8 + 1, count))
    expected = sum(length * count for length, count in rows)
    _require(expected <= 256 * 1024 * 1024, 'PNG exceeds execution decoded limit')
    offset, compressed, palette, ended_idat, seen_idat = 8, [], False, False, False
    while offset < len(data):
        length, kind = struct.unpack('>I4s', data[offset:offset + 8])
        payload = data[offset + 8:offset + 8 + length]
        if kind == b'PLTE':
            _require(not seen_idat and not palette and length % 3 == 0 and 0 < length <= 768,
                     'invalid PNG palette')
            palette = True
        if kind == b'IDAT':
            _require(not ended_idat, 'noncontiguous PNG data')
            seen_idat = True
            compressed.append(payload)
        elif seen_idat:
            ended_idat = True
        _require(kind in (b'IHDR', b'PLTE', b'IDAT', b'IEND') or bool(kind[0] & 32),
                 'unknown critical PNG chunk')
        offset += length + 12
    _require(color != 3 or palette, 'PNG palette missing')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(b''.join(compressed), expected + 1)
    except zlib.error:
        _require(False, 'invalid PNG compressed data')
    _require(len(raw) == expected and decoder.eof and not decoder.unused_data
             and not decoder.unconsumed_tail, 'invalid PNG pixel stream')
    offset = 0
    for length, count in rows:
        for _ in range(count):
            _require(raw[offset] <= 4, 'invalid PNG scanline filter')
            offset += length
    return width, height
