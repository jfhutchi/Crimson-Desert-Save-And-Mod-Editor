"""Parse + serialize PA's characteractionpackagedescription.xml.

PA uses a non-standard XML dialect: closing tags are `</>` (no name).
Each top-level package is `<PackageName ATTRS>...subpackages...</>`. Each
SubPackage child is `<SubPackage SlotName="X" FileName="Y"/>`.

This is the surgical-extension target. Adding a single `<SubPackage>` line to
`<Player_Kliff>` gives Kliff that weapon class WITHOUT swapping his whole
runtime package (which the field-test confirmed breaks skills/inventory).

Lives in PAZ 0010 at `actionchart/xml/description/characteractionpackagedescription.xml`.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class SubPackage:
    slot: str
    file: str

    def to_xml(self) -> str:
        return f'<SubPackage SlotName="{self.slot}" FileName="{self.file}"/>'


@dataclass
class Package:
    name: str
    base_file: str
    base_slot: str
    subpackages: list[SubPackage] = field(default_factory=list)
    raw_block: str = ''  # original XML text — used for surgical replacement

    def slots(self) -> set[str]:
        return {sp.slot for sp in self.subpackages}

    def slot_files(self) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {}
        for sp in self.subpackages:
            d.setdefault(sp.slot, []).append(sp.file)
        return d

    def has_slot_file(self, slot: str, file: str) -> bool:
        return any(sp.slot == slot and sp.file == file for sp in self.subpackages)

    def add_subpackage(self, slot: str, file: str) -> bool:
        """Add a SubPackage if not already present. Returns True if added."""
        if self.has_slot_file(slot, file):
            return False
        self.subpackages.append(SubPackage(slot=slot, file=file))
        return True

    def remove_subpackage(self, slot: str, file: str) -> bool:
        n = len(self.subpackages)
        self.subpackages = [
            sp for sp in self.subpackages
            if not (sp.slot == slot and sp.file == file)
        ]
        return len(self.subpackages) != n


_PKG_RE = re.compile(
    r'(?P<full><(?P<name>[A-Z_][A-Za-z0-9_]+)(?P<attrs>\s+[^>]*?)>'
    r'(?P<body>.*?)</>)',
    re.DOTALL,
)
_SUB_RE = re.compile(
    r'<SubPackage\s+SlotName="(?P<slot>[^"]+)"\s+FileName="(?P<file>[^"]+)"\s*/>',
    re.DOTALL,
)
_FILE_ATTR_RE = re.compile(r'FileName="([^"]+)"')
_SLOT_ATTR_RE = re.compile(r'SlotName="([^"]+)"')

# Top-level wrappers we don't want to confuse with packages
_RESERVED = {
    'Root', 'SlotName', 'Slot', 'SubPackage', 'CharacterPackage',
    'CharacterPackageList',
}


def parse_descriptor(xml_text: str) -> dict[str, Package]:
    """Extract every Package from the descriptor XML, keyed by name."""
    packages: dict[str, Package] = {}
    for m in _PKG_RE.finditer(xml_text):
        name = m.group('name')
        if name in _RESERVED:
            continue
        attrs = m.group('attrs')
        body = m.group('body')
        full = m.group('full')

        fname_m = _FILE_ATTR_RE.search(attrs)
        slot_m = _SLOT_ATTR_RE.search(attrs)
        # Some packages don't have FileName/SlotName at the root — that's ok
        subs = [
            SubPackage(slot=sm.group('slot'), file=sm.group('file'))
            for sm in _SUB_RE.finditer(body)
        ]
        # Skip Slot-list and other inner wrappers that match our regex but
        # have no actual SubPackages and no useful root attrs
        if not subs and not fname_m and not slot_m:
            continue
        packages[name] = Package(
            name=name,
            base_file=fname_m.group(1) if fname_m else '',
            base_slot=slot_m.group(1) if slot_m else '',
            subpackages=subs,
            raw_block=full,
        )
    return packages


def serialize_package(pkg: Package, indent: str = '\t\t') -> str:
    """Produce the XML block for a single package, preserving PA's </> syntax."""
    lines = []
    open_tag = f'<{pkg.name}'
    if pkg.base_file:
        open_tag += f' FileName="{pkg.base_file}"'
    if pkg.base_slot:
        open_tag += f' SlotName="{pkg.base_slot}"'
    open_tag += '>'
    lines.append(open_tag)
    for sp in pkg.subpackages:
        lines.append(f'\t{sp.to_xml()}')
    lines.append('</>')
    return '\n'.join(indent + line for line in lines)


def patch_descriptor(xml_text: str, modified_packages: dict[str, Package]) -> str:
    """Apply additions/removals to packages in the source XML, preserving
    all original whitespace, comments, and attributes for untouched lines.

    Strategy: for each modified package, compute the diff vs the original,
    then INSERT new SubPackage lines directly before </> in the source text.
    Removals are handled by deleting matching <SubPackage .../> lines. We
    never re-serialize the whole block — comments and indentation survive.
    """
    original = parse_descriptor(xml_text)
    out = xml_text
    for name, new_pkg in modified_packages.items():
        old_pkg = original.get(name)
        if old_pkg is None:
            log.warning("patch_descriptor: '%s' not in source XML", name)
            continue
        old_pairs = {(sp.slot, sp.file) for sp in old_pkg.subpackages}
        new_pairs = {(sp.slot, sp.file) for sp in new_pkg.subpackages}
        added = [(s, f) for s, f in new_pairs - old_pairs]
        removed = [(s, f) for s, f in old_pairs - new_pairs]

        block = old_pkg.raw_block

        # Remove any (slot, file) pairs that were dropped
        for slot, fname in removed:
            line_pat = re.compile(
                r'\n[ \t]*<SubPackage\s+SlotName="' + re.escape(slot)
                + r'"\s+FileName="' + re.escape(fname) + r'"\s*/>'
            )
            block = line_pat.sub('', block, count=1)

        # Insert added pairs immediately before </>
        if added:
            # Find the indent of an existing SubPackage line in the block
            indent_m = re.search(r'(?m)^([ \t]+)<SubPackage\s', block)
            indent = indent_m.group(1) if indent_m else '\t\t\t'
            insertion = ''.join(
                f'\n{indent}<SubPackage SlotName="{s}" FileName="{f}"/>'
                for s, f in added
            )
            # Insert before the final </>
            close_idx = block.rfind('</>')
            if close_idx == -1:
                log.warning("patch_descriptor: no </> in block for '%s'", name)
                continue
            block = block[:close_idx] + insertion + '\n\t\t' + block[close_idx:]

        if block != old_pkg.raw_block and old_pkg.raw_block in out:
            out = out.replace(old_pkg.raw_block, block, 1)
        elif block != old_pkg.raw_block:
            log.warning("patch_descriptor: old raw_block for '%s' not found in source",
                        name)
    return out


def diff_packages(a: Package, b: Package) -> dict:
    """Return a-only / b-only / shared slot-file pairs."""
    a_pairs = {(sp.slot, sp.file) for sp in a.subpackages}
    b_pairs = {(sp.slot, sp.file) for sp in b.subpackages}
    return {
        'a_only':  sorted(a_pairs - b_pairs),
        'b_only':  sorted(b_pairs - a_pairs),
        'shared':  sorted(a_pairs & b_pairs),
        'a_slots': sorted({s for s, _ in a_pairs}),
        'b_slots': sorted({s for s, _ in b_pairs}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────
def _selftest() -> None:
    p = (r'C:\Users\Coding\CrimsonDesertModding\extracted_actionchart\\'
         r'characteractionpackagedescription.xml')
    if not os.path.isfile(p):
        print("self-test: descriptor not extracted; skip")
        return
    with open(p, encoding='utf-8') as f:
        xml = f.read()
    pkgs = parse_descriptor(xml)
    print(f"parsed {len(pkgs)} packages")

    kliff = pkgs['Player_Kliff']
    damian = pkgs['Player_PHW']
    print(f"\nKliff slots: {sorted(kliff.slots())}")
    print(f"Damian slots: {sorted(damian.slots())}")
    d = diff_packages(kliff, damian)
    print(f"Damian-only: {d['b_only']}")

    # Try injecting Pistol into Kliff
    pistol = next(sp for sp in damian.subpackages if sp.slot == 'Pistol')
    added = kliff.add_subpackage(pistol.slot, pistol.file)
    print(f"\nInjected Pistol into Kliff: {added}")
    print(f"Kliff now has {len(kliff.subpackages)} subpackages")

    # Patch the source XML
    patched = patch_descriptor(xml, {'Player_Kliff': kliff})
    delta = len(patched) - len(xml)
    print(f"Patched XML: {len(patched)}B (delta {delta:+d}B)")

    # Sanity: re-parse
    repkgs = parse_descriptor(patched)
    new_kliff = repkgs['Player_Kliff']
    print(f"After patch + re-parse: Kliff slots = {sorted(new_kliff.slots())}")
    print(f"Pistol present: {'Pistol' in new_kliff.slots()}")


if __name__ == '__main__':
    _selftest()
