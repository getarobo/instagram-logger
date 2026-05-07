"""Tiny placeholder media bytes for the fake IG client.

The fake IG client (`backend.ig_client.fake`) hands the ingester `file://`
URLs that point at on-disk copies of these blobs. Tests only exercise the
byte-stream + sha + atomic-rename pipeline, so the contents only need to
be deterministic, small, and yield a correct Content-Length.

- JPEG: 64x64 solid red (~700 B), generated once with Pillow then frozen
  here as a hex literal so the runtime has no Pillow dependency.
- MP4 : minimal ISO BMFF container (~300 B). Browsers will not play it
  but ingest does not care.
"""

from __future__ import annotations

from pathlib import Path

# 64x64 red JPEG, quality=70. Captured once via Pillow; held as a hex
# literal so the package has no PIL runtime dep.
_JPEG_HEX = (
    "ffd8ffe000104a46494600010100000100010000ffdb0043000a07070807060a0808080b0a0a0b0e"
    "18100e0d0d0e1d15161118231f2524221f2221262b372f26293429212230413134393b3e3e3e252e"
    "4449433c48373d3e3bffdb0043010a0b0b0e0d0e1c10101c3b2822283b3b3b3b3b3b3b3b3b3b3b3b"
    "3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b"
    "3b3bffc00011080040004003012200021101031101ffc4001f00000105010101010101000000000"
    "00000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300"
    "041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a"
    "25262728292a3435363738393a434445464748494a535455565758595a636465666768696a73747"
    "5767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9"
    "bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faff"
    "c4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100020"
    "102040403040705040400010277000102031104052131061241510761711322328108144291a1b1"
    "c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494"
    "a535455565758595a636465666768696a737475767778797a82838485868788898a929394959697"
    "98999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2"
    "e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00e568a28af9d3f660a"
    "28a2800a28a2800a28a2800a28a2800a28a2800a28a2800a28a2800a28a2800a28a2800a28a2800"
    "a28a2800a28a2800a28a2800a28a2800a28a2803ffd9"
)
_MINIMAL_JPEG = bytes.fromhex(_JPEG_HEX)

# Minimal MP4 ISO BMFF container: ftyp + free padding. Not playable, but
# valid enough for hashing/streaming.
_MINIMAL_MP4 = (
    b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    b"\x00\x00\x00\x08free"
    + b"\x00" * 256
)


def jpeg_bytes() -> bytes:
    return _MINIMAL_JPEG


def mp4_bytes() -> bytes:
    return _MINIMAL_MP4


def ensure_placeholders(root: Path) -> tuple[Path, Path]:
    """Materialize a JPEG and an MP4 under `root` (idempotent).

    Returns the absolute paths so callers can build `file://` URLs.
    """
    root.mkdir(parents=True, exist_ok=True)
    jpg = root / "red_64x64.jpg"
    mp4 = root / "silent_1s.mp4"
    if not jpg.exists() or jpg.stat().st_size != len(_MINIMAL_JPEG):
        jpg.write_bytes(_MINIMAL_JPEG)
    if not mp4.exists() or mp4.stat().st_size != len(_MINIMAL_MP4):
        mp4.write_bytes(_MINIMAL_MP4)
    return jpg, mp4


__all__ = ["jpeg_bytes", "mp4_bytes", "ensure_placeholders"]
