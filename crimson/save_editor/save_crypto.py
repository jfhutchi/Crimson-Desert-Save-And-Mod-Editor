from __future__ import annotations

import hashlib
import hmac
import datetime
import logging
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import lz4.block
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from crimson.save_editor.app_logging import new_operation_id, phase
from crimson.save_editor.models import SaveData

if TYPE_CHECKING:
    from crimson.save_editor.save_compat import SaveSchemaIdentity

log = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class SaveWriteResult:
    destination: Path
    backup_path: Path
    output_sha256: str
    byte_count: int


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


def load_save_file(
    path: str,
    operation_id: str | None = None,
    *,
    detect_schema: bool = True,
) -> SaveData:
    operation_id = operation_id or new_operation_id("load")
    with phase(log, operation_id, "file_read", path=path):
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
    log.info(
        "operation=%s save_header version=%s file_bytes=%s compressed_bytes=%s "
        "decompressed_bytes=%s",
        operation_id,
        version,
        len(file_data),
        payload_size,
        uncomp_size,
    )

    ciphertext = file_data[PAYLOAD_OFFSET:PAYLOAD_OFFSET + payload_size]

    with phase(log, operation_id, "decryption", encrypted_bytes=len(ciphertext)):
        compressed = chacha20_crypt(ciphertext, nonce, key)

    with phase(log, operation_id, "hmac_verification", compressed_bytes=len(compressed)):
        hmac_ok = verify_hmac(compressed, stored_hmac, key)

    with phase(log, operation_id, "decompression", expected_bytes=uncomp_size):
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
        schema_identity=None,
        compatibility_profile_id=None,
        is_schema_supported=False,
    )

    if detect_schema:
        from crimson.save_editor.save_compat import compute_schema_identity, load_profiles, match_profile

        with phase(log, operation_id, "schema_detection"):
            identity = compute_schema_identity(bytes(save_data.decompressed_blob), header)
            profile = match_profile(identity, load_profiles())
        save_data.schema_identity = identity
        save_data.compatibility_profile_id = profile.profile_id if profile else None
        save_data.is_schema_supported = profile is not None
        log.info(
            "operation=%s schema_result supported=%s profile=%s schema_sha256=%s",
            operation_id,
            save_data.is_schema_supported,
            save_data.compatibility_profile_id,
            identity.schema_sha256,
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
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(
            "Refusing to overwrite an existing save without a verified backup; "
            "use transactional_write_save"
        )
    if original_header is None:
        raise ValueError("An original save header is required for serialization")
    destination.write_bytes(
        serialize_save_bytes(
            bytes(edited_blob),
            original_header,
            new_operation_id("compat-write"),
        )
    )


def serialize_save_bytes(
    edited_blob: bytes,
    original_header: bytes,
    operation_id: str,
) -> bytes:
    if len(original_header) < HEADER_SIZE:
        raise ValueError(f"Original save header must contain {HEADER_SIZE} bytes")
    version = struct.unpack_from("<H", original_header, VERSION_OFFSET)[0]
    key = _generate_save_key(version)
    with phase(log, operation_id, "serialization", input_bytes=len(edited_blob)):
        compressed = lz4.block.compress(
            edited_blob,
            store_size=False,
            mode="high_compression",
            compression=3,
        )
    with phase(log, operation_id, "hmac", compressed_bytes=len(compressed)):
        digest = compute_hmac(compressed, key)
    nonce = os.urandom(16)
    with phase(log, operation_id, "encryption", compressed_bytes=len(compressed)):
        encrypted = chacha20_crypt(compressed, nonce, key)
    header = bytearray(original_header[:HEADER_SIZE])
    header[0:4] = b"SAVE"
    struct.pack_into("<H", header, VERSION_OFFSET, version)
    struct.pack_into("<H", header, FLAGS_OFFSET, 0x0080)
    struct.pack_into("<I", header, UNCOMP_SIZE_OFFSET, len(edited_blob))
    struct.pack_into("<I", header, PAYLOAD_SIZE_OFFSET, len(compressed))
    header[NONCE_OFFSET:NONCE_OFFSET + 16] = nonce
    header[HMAC_OFFSET:HMAC_OFFSET + 32] = digest
    return bytes(header) + encrypted


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transactional_write_save(
    destination: str | Path,
    edited_blob: bytes,
    original_header: bytes,
    backup_source: str | Path,
    expected_identity: SaveSchemaIdentity | None,
    operation_id: str,
    scope_family_id: str | None = None,
    *,
    expected_protected_source_sha256: str | None = None,
) -> SaveWriteResult:
    from crimson.save_editor.save_compat import (
        UnknownSaveSchemaError,
        compute_schema_identity,
        load_profiles,
        require_supported_identity,
        schema_structure_matches,
    )

    destination = Path(destination)
    backup_source = Path(backup_source)
    if expected_identity is None:
        raise UnknownSaveSchemaError("Loaded save has no schema identity")
    if scope_family_id is None:
        require_supported_identity(expected_identity, load_profiles())
    else:
        from crimson.save_editor.blackstar_compat import BLACKSTAR_FAMILY_ID
        if scope_family_id != BLACKSTAR_FAMILY_ID:
            raise UnknownSaveSchemaError("Unknown scoped write authorization")
    candidate_identity = compute_schema_identity(edited_blob, original_header)
    if not schema_structure_matches(candidate_identity, expected_identity):
        raise UnknownSaveSchemaError("Edited blob schema differs from the loaded schema")
    if destination.exists() and not destination.is_file():
        raise IsADirectoryError(f"Save destination is not a file: {destination}")

    destination_was_present = destination.is_file()
    occupied_save_as = (
        destination_was_present
        and destination.resolve() != backup_source.resolve()
    )
    # Save As may target another occupied slot. Preserve the bytes that are
    # actually about to be replaced; for a new path, preserve the loaded source.
    protected_source = destination if destination_was_present else backup_source
    if not protected_source.is_file():
        raise FileNotFoundError(f"Backup source does not exist: {protected_source}")

    backup_dir = protected_source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"{protected_source.name}.{stamp}.bak"
    backup_verified = False
    try:
        with phase(
            log,
            operation_id,
            "backup",
            source=protected_source,
            destination=backup_path,
        ):
            source_hash = _sha256_file(protected_source)
            if (
                expected_protected_source_sha256 is not None
                and not hmac.compare_digest(
                    source_hash, expected_protected_source_sha256
                )
            ):
                if scope_family_id is not None:
                    from crimson.save_editor.blackstar_compat import BlackstarCompatibilityError

                    raise BlackstarCompatibilityError(
                        "Source file changed after preview"
                    )
                raise RuntimeError("Protected save source changed before backup")
            source_size = protected_source.stat().st_size
            shutil.copy2(protected_source, backup_path)
            if backup_path.stat().st_size != source_size:
                raise OSError("Backup size verification failed")
            if _sha256_file(backup_path) != source_hash:
                raise OSError("Backup SHA-256 verification failed")
            backup_verified = True
        if occupied_save_as:
            with phase(
                log,
                operation_id,
                "backup_validation",
                destination=backup_path,
            ):
                backup_save = load_save_file(str(backup_path))
                if backup_save.schema_identity is None:
                    raise UnknownSaveSchemaError(
                        "Occupied Save As destination has no schema identity"
                    )
                require_supported_identity(
                    backup_save.schema_identity, load_profiles()
                )
                if not schema_structure_matches(
                    backup_save.schema_identity, expected_identity
                ):
                    raise UnknownSaveSchemaError(
                        "Occupied Save As destination schema differs from the loaded schema"
                    )
    except Exception:
        if backup_path.exists() and not backup_verified:
            backup_path.unlink()
        raise

    serialized = serialize_save_bytes(edited_blob, original_header, operation_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temp_path = Path(temp_name)
    try:
        with phase(
            log,
            operation_id,
            "temporary_write",
            destination=temp_path,
            bytes=len(serialized),
        ):
            with os.fdopen(fd, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        with phase(log, operation_id, "temporary_validation", destination=temp_path):
            reloaded = load_save_file(str(temp_path), detect_schema=False)
            reloaded_blob = bytes(reloaded.decompressed_blob)
            if (
                hashlib.sha256(reloaded_blob).digest()
                != hashlib.sha256(edited_blob).digest()
                or reloaded_blob != edited_blob
            ):
                raise ValueError("Temporary save decompressed hash mismatch")
        with phase(log, operation_id, "final_write", destination=destination):
            if destination_was_present:
                if (
                    not destination.is_file()
                    or _sha256_file(destination) != source_hash
                ):
                    raise RuntimeError(
                        "Save destination changed after its verified backup; "
                        "refusing to overwrite newer bytes"
                    )
            elif destination.exists():
                raise FileExistsError(
                    "Save destination appeared after backup validation; refusing to "
                    "overwrite an unprotected file"
                )
            os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return SaveWriteResult(
        destination=destination,
        backup_path=backup_path,
        output_sha256=_sha256_file(destination),
        byte_count=destination.stat().st_size,
    )


def transactional_write_blackstar(
    destination: str | Path,
    edited_blob: bytes,
    original_header: bytes,
    expected_identity: SaveSchemaIdentity,
    token,
    generation: int,
    operation_id: str,
    *,
    loaded_blob: bytes,
) -> SaveWriteResult:
    from crimson.save_editor.blackstar_compat import (
        BLACKSTAR_FAMILY_ID,
        BlackstarCompatibilityError,
    )

    destination = Path(destination).resolve()
    if token.family_id != BLACKSTAR_FAMILY_ID:
        raise BlackstarCompatibilityError("Apply token family mismatch")
    if str(destination) != token.source_path:
        raise BlackstarCompatibilityError("Apply token path mismatch")
    if generation != token.document_generation:
        raise BlackstarCompatibilityError("Apply token generation is stale")
    if not destination.is_file():
        raise FileNotFoundError(destination)
    source_file_hash = _sha256_file(destination)
    if source_file_hash != token.source_file_sha256:
        raise BlackstarCompatibilityError("Source file changed after preview")
    if expected_identity is None:
        raise BlackstarCompatibilityError("Save has no schema identity")
    if expected_identity.schema_sha256 != token.source_schema_sha256:
        raise BlackstarCompatibilityError("Source schema changed after preview")
    if not hmac.compare_digest(
        hashlib.sha256(loaded_blob).hexdigest(), token.source_blob_sha256
    ):
        raise BlackstarCompatibilityError("Source blob changed after preview")
    if hashlib.sha256(edited_blob).hexdigest() != token.candidate_blob_sha256:
        raise BlackstarCompatibilityError("Candidate hash differs from preview")
    return transactional_write_save(
        destination=destination,
        edited_blob=edited_blob,
        original_header=original_header,
        backup_source=destination,
        expected_identity=expected_identity,
        operation_id=operation_id,
        scope_family_id=BLACKSTAR_FAMILY_ID,
        expected_protected_source_sha256=token.source_file_sha256,
    )
