class VirtualNode:
    """Represents a single file or directory in the VFS."""

    KNOWN_FORMATS = {
        "paver": "Pearl Abyss Version",
        "save": "Save Template",

        "paz": "Pearl Abyss Zip",
        "pamt": "Pearl Abyss Meta Table",
        "papgt": "Pearl Abyss Pack Group Tree",
        "pabgb": "Pearl Abyss Binary Group Body",
        "pabgh": "Pearl Abyss Binary Group Header",

        "binarystring": "Packed String List",
        "paloc": "Localization Strings",

        "paseq": "Pearl Abyss Sequencer",
        "paseqc": "Pearl Abyss Sequencer Compiled",
        "paseqh": "Pearl Abyss Sequencer Header",
        "pastage": "Pearl Abyss Stage",

        "paschedule": "Pearl Abyss Schedule",
        "paschedulepath": "Pearl Abyss Schedule Path",
        "paschedulectx": "Pearl Abyss Schedule Context",
        "pai": "Pearl Abyss AI",
        "paatt": "Pearl Abyss Attack",

        "paa": "Pearl Abyss Animation",
        "paasmt": "Pearl Abyss Animation Set Matching Table",
        "pamlod": "Pearl Abyss Mesh LOD",

        "pac":"Pearl Abyss Character",
        "pac_xml":"Pearl Abyss Character XML",
        "pab":"Pearl Abyss Bones",
        "pab.sockets.xml":"Pearl Abyss Sockets Metadata",
        "pam":"Pearl Abyss Mesh",
        "pami":"Pearl Abyss Mesh Info",
        "pat":"Pearl Abyss Tree",
        "pati":"Pearl Abyss Tree Info",
        "pappt":"Pearl Abyss Part Prefab Table",
        "padock":"Pearl Abyss Docking",
        "patag":"Pearl Abyss Tag",
        "pareflect":"Pearl Abyss Reflection",
        "pampg":"Pearl Abyss Mesh Proxy Group",
        "pamhc":"Pearl Abyss Model Property Header Collection",

        "palevel": "Pearl Abyss Level",
        "palevel_xml": "Pearl Abyss Level XML",
        "paem": "Pearl Abyss Emitter",

        "pacpp": "Pearl Abyss C++",
        "pacpph": "Pearl Abyss C++ Header",
        "pacpp.o": "Pearl Abyss C++ Object File",
        "padxil": "Pearl Abyss DXIL",
        "pagputracer": "GPU Profiler Tracer",

        "dds": "Direct Draw Surface",
        "pathc": "Pearl Abyss Texture/Path Cache",

        "bnk": "Wwise Sound Bank",
        "wem": "Wwise Encoded Audio",
        "pasound": "Pearl Abyss Sound Metadata",

        "css": "Cascading Style Sheet",
        "html": "Hypertext Markup Language",
        "thtml": "Template HTML",
        "xml": "Extensible Markup Language",
        "txt": "Standard Text File",
        "png": "Portable Network Graphics",
        "ttf":"TrueType Font",
        "mp4":"MPEG-4 Video",
        "cur":"Cursor",

        "hkx": "Havoc Native File",
        # "": "",
    }

    LANGUAGES = {
        0x3FFF: "ALL",  # Language.ALL
        0x7FFF: "ALL",  # Language.ALL (1.10+)
        0x0001: "KOR",  # Language.Korean
        0x0002: "ENG",  # Language.English
        0x0004: "JPN",  # Language.Japanese
        0x0008: "RUS",  # Language.Russian
        0x0010: "TUR",  # Language.Turkish
        0x0020: "SPA-ES",  # Language.Spanish
        0x0040: "SPA-MX",  # Language.Mexican
        0x0080: "FRE",  # Language.French
        0x0100: "DEU",  # Language.German
        0x0200: "ITA",  # Language.Italian
        0x0400: "POL",  # Language.Polish
        0x0800: "POR",  # Language.Portuguese
        0x1000: "ZHO-TW",  # Language.Taiwanese
        0x2000: "ZHO-CN",  # Language.Chinese
        0x4000: "THA",  # Language.Thai (1.10+)
    }

    def __init__(self, name: str, is_dir: bool, parent=None):
        self.name = name
        self.is_dir = is_dir
        self.parent_item = parent
        self.is_populated = False  # Still useful to track if we have loaded children for lazy loading
        self.child_items = []

    def append_child(self, child):
        self.child_items.append(child)

    def child(self, row: int):
        if 0 <= row < len(self.child_items):
            return self.child_items[row]
        return None

    def child_count(self) -> int:
        return len(self.child_items)

    def row(self) -> int:
        if self.parent_item:
            # Find index within the parent's list of children
            try:
                return self.parent_item.child_items.index(self)
            except ValueError:
                return 0  # Should not happen if parent is correct
        return 0

    @property
    def absolute_path(self) -> str:
        """Recursively builds the absolute path, ignoring the hidden Root node."""
        # 1. Base case: If this is the hidden root, return an empty string
        if self.name == "Root":
            return ""

        # 2. Base case: If the parent is the root, this is a top-level folder
        if self.parent_item is None or self.parent_item.name == "Root":
            return self.name

        # 3. Recursive case: Build path from the parent down
        return f"{self.parent_item.absolute_path}/{self.name}"
