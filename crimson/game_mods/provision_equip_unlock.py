"""
Provision Item → Equippable Equipment Transformer

Clones a real working donor item (same equip_type_info category) and swaps
in the provision item's identity (key, name, icon, visual mesh). The result
is a fully functional equipment item that looks like the provision item.

Usage:
    from crimson.game_mods.provision_equip_unlock import unlock_provision_items, _build_donor_map
    donor_map = _build_donor_map(items)  # call BEFORE clearing tribe_gender
    count = unlock_provision_items(items, donor_map=donor_map)
"""

import copy
import logging

log = logging.getLogger(__name__)


def _safe_iv(v):
    if isinstance(v, dict):
        return v.get('a', v.get('value', 0))
    return v or 0


# Fields to preserve from the original provision item (identity + visuals)
_KEEP_FROM_ORIGINAL = {
    'key', 'string_key', 'item_name', 'item_desc', 'item_desc2',
    'item_icon_list', 'map_icon_path', 'prefab_data_list',
    'gimmick_info', 'item_memo', 'filter_type',
}


def _build_donor_map(items):
    """Build a map of equip_type_info hash → best donor item.

    Best donor = real equippable item with the most enchant levels.
    Must have non-zero stats AND tribe_gender_list (real equipment).
    Call this BEFORE clearing tribe_gender on items.
    """
    donors = {}
    for it in items:
        eti = _safe_iv(it.get('equip_type_info', 0))
        if not eti:
            continue
        edl = it.get('enchant_data_list', [])
        if not edl:
            continue
        has_stats = False
        for ed in edl:
            sd = ed.get('enchant_stat_data', {})
            for s in (sd.get('stat_list_static') or []):
                if _safe_iv(s.get('change_mb', 0)) != 0:
                    has_stats = True
                    break
            if has_stats:
                break
        if not has_stats:
            continue

        has_tribe = False
        for pd in (it.get('prefab_data_list') or []):
            if pd.get('tribe_gender_list'):
                has_tribe = True
                break
        if not has_tribe:
            continue

        existing = donors.get(eti)
        if existing is None or len(edl) > len(existing.get('enchant_data_list', [])):
            donors[eti] = it

    return donors


def _is_provision_item(it):
    """Check if an item is a provision/treasure that could be made equippable."""
    eti = _safe_iv(it.get('equip_type_info', 0))
    if not eti:
        return False
    edl = it.get('enchant_data_list', [])
    for ed in edl:
        sd = ed.get('enchant_stat_data', {})
        for s in (sd.get('stat_list_static') or []):
            if _safe_iv(s.get('change_mb', 0)) != 0:
                return False
        for s in (sd.get('stat_list_static_level') or []):
            if _safe_iv(s.get('change_mb', 0)) != 0:
                return False
    pdl = it.get('prefab_data_list', [])
    if not pdl:
        return False
    return True


def unlock_provision_items(items, donor_map=None):
    """Transform provision items by cloning donor data and keeping original identity.

    For each provision item: clone the entire donor item, then swap back
    the original's key, name, icon, and visual mesh. Clear tribe_gender_list.
    """
    if donor_map is None:
        donor_map = _build_donor_map(items)
    log.info("Equip unlock: built donor map with %d equip_type categories", len(donor_map))

    transformed = 0
    tg_cleared = 0
    stats_added = 0
    no_donor = 0

    for i, it in enumerate(items):
        eti = _safe_iv(it.get('equip_type_info', 0))
        if not eti:
            continue

        if not _is_provision_item(it):
            # Not a provision — just clear tribe_gender
            for pd in (it.get('prefab_data_list') or []):
                tg = pd.get('tribe_gender_list')
                if tg:
                    pd['tribe_gender_list'] = []
                    tg_cleared += 1
            continue

        donor = donor_map.get(eti)
        if not donor:
            # No donor — just clear tribe_gender
            for pd in (it.get('prefab_data_list') or []):
                tg = pd.get('tribe_gender_list')
                if tg:
                    pd['tribe_gender_list'] = []
                    tg_cleared += 1
            no_donor += 1
            continue

        # Save original identity fields
        saved = {}
        for field in _KEEP_FROM_ORIGINAL:
            if field in it:
                saved[field] = it[field]

        # Clone the entire donor
        cloned = copy.deepcopy(donor)

        # Swap identity back from original
        for field, val in saved.items():
            cloned[field] = val

        # Clear tribe_gender on the cloned item
        for pd in (cloned.get('prefab_data_list') or []):
            if pd.get('tribe_gender_list'):
                pd['tribe_gender_list'] = []
                tg_cleared += 1

        # Replace in the list
        items[i] = cloned
        transformed += 1
        stats_added += 1

    log.info("Equip unlock: %d items cloned from donors, %d tribe cleared, "
             "%d no donor found, %d donor categories",
             transformed, tg_cleared, no_donor, len(donor_map))

    return transformed, tg_cleared, stats_added, stats_added
