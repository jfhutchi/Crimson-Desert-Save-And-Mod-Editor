try:
    from crimson.game_mods.dmm_parser import *
    from crimson.game_mods import dmm_parser as _dmm
    if not hasattr(_dmm, 'extract_file'):
        raise ImportError("dmm_parser missing native functions")
except ImportError as e:
    print(f"Error: {e}")
    from crimson.game_mods.crimson_rs.crimson_rs import *
from crimson.game_mods.crimson_rs.enums import Compression, Crypto, Language
