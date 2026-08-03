from __future__ import annotations

import hashlib
import hmac
import os
import struct
import tempfile
from typing import Tuple

import lz4.block
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from crimson.game_mods.models import SaveData


_SAVE_BASE_KEY = bytes.fromhex(
    "C41B8E730DF259A637CC04E9B12F9668DA107A853E61F9224DB80AD75C13EF90"
)[:31]

_VERSION_PREFIXES = {
    1: b'^Qgbrm/.#@`zsr]\\@rvfal#"',
    2: b"^Pearl--#Abyss__@!!",
}


def _generate_save_key(version: int) -> bytes:
    prefix = _VERSION_PREFIXES.get(version)
    if prefix is None:
        raise ValueError(f"Unsupported save version {version}")
    material = prefix + b"PRIVATE_HMAC_SECRET_CHECK"
    return bytes(x ^ y for x, y in zip(_SAVE_BASE_KEY, material)) + b"\x00"


KEY = _generate_save_key(2)

HEADER_SIZE = 0x80
MAGIC_OFFSET = 0x00
VERSION_OFFSET = 0x04
FLAGS_OFFSET = 0x06
UNCOMP_SIZE_OFFSET = 0x12
PAYLOAD_SIZE_OFFSET = 0x16
NONCE_OFFSET = 0x1A
HMAC_OFFSET = 0x2A
PAYLOAD_OFFSET = 0x80


def chacha20_crypt(data: bytes, nonce16: bytes, key: bytes = None) -> bytes:
    if key is None:
        key = KEY
    cipher = Cipher(algorithms.ChaCha20(key, nonce16), mode=None)
    encryptor = cipher.encryptor()
    return encryptor.update(data) + encryptor.finalize()


def compute_hmac(data: bytes, key: bytes = None) -> bytes:
    if key is None:
        key = KEY
    return hmac.new(key, data, hashlib.sha256).digest()


def verify_hmac(data: bytes, expected: bytes, key: bytes = None) -> bool:
    return hmac.compare_digest(compute_hmac(data, key), expected)


def load_save_file(path: str) -> SaveData:
    with open(path, "rb") as f:
        file_data = f.read()

    if len(file_data) < HEADER_SIZE + 16:
        raise ValueError("File too small to be a save file.")

    magic = file_data[MAGIC_OFFSET:MAGIC_OFFSET + 4]
    if magic != b"SAVE":
        raise ValueError(f"Bad magic: expected 'SAVE', got {magic!r}")

    version = struct.unpack_from("<H", file_data, VERSION_OFFSET)[0]
    uncomp_size = struct.unpack_from("<I", file_data, UNCOMP_SIZE_OFFSET)[0]
    payload_size = struct.unpack_from("<I", file_data, PAYLOAD_SIZE_OFFSET)[0]
    nonce = file_data[NONCE_OFFSET:NONCE_OFFSET + 16]
    stored_hmac = file_data[HMAC_OFFSET:HMAC_OFFSET + 32]

    if PAYLOAD_OFFSET + payload_size > len(file_data):
        raise ValueError(
            f"Payload size {payload_size} exceeds file size {len(file_data)}"
        )

    key = _generate_save_key(version)

    ciphertext = file_data[PAYLOAD_OFFSET:PAYLOAD_OFFSET + payload_size]

    compressed = chacha20_crypt(ciphertext, nonce, key)

    hmac_ok = verify_hmac(compressed, stored_hmac, key)

    decompressed = lz4.block.decompress(
        compressed, uncompressed_size=uncomp_size
    )

    if len(decompressed) != uncomp_size:
        raise ValueError(
            f"LZ4 decompressed {len(decompressed)} bytes, expected {uncomp_size}"
        )

    header = file_data[:HEADER_SIZE]

    save_data = SaveData(
        source_file_sha256=hashlib.sha256(file_data).hexdigest(),
        raw_header=header,
        decompressed_blob=bytearray(decompressed),
        original_compressed_size=payload_size,
        original_decompressed_size=uncomp_size,
        file_path=path,
        is_raw_stream=False,
    )

    if not hmac_ok:
        raise Warning("HMAC mismatch - save may be corrupted but was loaded anyway.")

    return save_data


def load_raw_stream(path: str) -> SaveData:
    with open(path, "rb") as f:
        blob = f.read()
    return SaveData(
        source_file_sha256=hashlib.sha256(blob).hexdigest(),
        raw_header=b"",
        decompressed_blob=bytearray(blob),
        original_compressed_size=0,
        original_decompressed_size=len(blob),
        file_path=path,
        is_raw_stream=True,
    )


def write_save_file(
    path: str,
    edited_blob: bytes,
    original_header: bytes | None = None,
) -> None:
    version = 2
    if original_header and len(original_header) >= 6:
        version = struct.unpack_from("<H", original_header, VERSION_OFFSET)[0]
    key = _generate_save_key(version)

    compressed = lz4.block.compress(
        bytes(edited_blob),
        store_size=False,
        mode="high_compression",
        compression=3,
    )

    nonce = os.urandom(16)

    hmac_digest = compute_hmac(compressed, key)

    encrypted = chacha20_crypt(compressed, nonce, key)

    header = bytearray(HEADER_SIZE)

    if original_header and len(original_header) >= 0x12:
        header[:0x12] = original_header[:0x12]

    header[0:4] = b"SAVE"
    struct.pack_into("<H", header, VERSION_OFFSET, version)
    struct.pack_into("<H", header, FLAGS_OFFSET, 0x0080)

    struct.pack_into("<I", header, UNCOMP_SIZE_OFFSET, len(edited_blob))
    struct.pack_into("<I", header, PAYLOAD_SIZE_OFFSET, len(compressed))

    header[NONCE_OFFSET:NONCE_OFFSET + 16] = nonce
    header[HMAC_OFFSET:HMAC_OFFSET + 32] = hmac_digest

    # Write to a sibling temp file and rename over the destination. A plain
    # open(path, "wb") truncates the user's save before the first byte
    # lands, so any failure mid-write (disk full, AV, crash) leaves a
    # header-only stub and the original is gone.
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bytes(header))
            f.write(encrypted)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_timestamped_backup(save_path: str, keep: int = 10) -> str:
    """Copy ``save_path`` into a sibling ``backups/`` dir, timestamped.

    Returns the backup path ("" if the source does not exist). Callers used
    to copy to a fixed ``<save>.backup``, which meant the second edit in a
    session overwrote the only pristine copy with already-modified bytes.
    """
    if not os.path.isfile(save_path):
        return ""

    backup_dir = os.path.join(os.path.dirname(os.path.abspath(save_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    import datetime
    import shutil

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{os.path.basename(save_path)}.{stamp}.bak")
    shutil.copy2(save_path, backup_path)

    try:
        existing = sorted(
            (f for f in os.listdir(backup_dir)
             if f.endswith(".bak") and ".PRISTINE." not in f),
            key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)),
            reverse=True,
        )
        for old in existing[keep:]:
            os.remove(os.path.join(backup_dir, old))
    except OSError:
        pass

    return backup_path
