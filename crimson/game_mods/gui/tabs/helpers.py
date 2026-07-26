from __future__ import annotations

import logging

log = logging.getLogger(__name__)

def extract_file_data(
    game_dir: str = "",
    group_name: str = "",
    dir_path: str = "",
    file_name: str = "",
) -> bytes:
    try:
        from crimson.game_mods import dmm_parser as dmm
        file_data = dmm.extract_file(game_dir, group_name, dir_path, file_name)
    except IOError:
        log.error("Error: The PAZ file cannot be read!")
    except ValueError:
        log.error("Error: File not found in PAMT!")
    except Exception as e:
        log.error(f"Error: {e}")
    else:
        return file_data