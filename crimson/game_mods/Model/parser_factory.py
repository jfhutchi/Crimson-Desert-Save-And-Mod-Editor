"""Format detection and parser dispatch for BlackSpace Engine mesh files."""

import struct
from model_types import ParsedModel


PAC_VERSION = 0x01000903
PAM_VERSIONS = {0x00001802, 0x00001803, 0x01001806}


def get_parser(data: bytes):
    """Return the appropriate parser for the given file data."""
    if len(data) < 8 or data[:4] != b'PAR ':
        raise ValueError(f"Not a PAR file (magic: {data[:4]!r})")

    version = struct.unpack_from('<I', data, 4)[0]

    if version == PAC_VERSION:
        from pac_parser import PacParser
        return PacParser()

    if version in PAM_VERSIONS:
        from pam_parser import PamParser
        return PamParser()

    raise ValueError(f"Unknown PAR version: 0x{version:08X}")


def parse_auto(data: bytes, **kwargs) -> ParsedModel:
    """Detect format and parse in one call."""
    return get_parser(data).parse(data, **kwargs)
