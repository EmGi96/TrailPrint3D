"""Installed-font listing for the picker pages' Text Settings font dropdown
(assets/shape_extras.js, served by picker_server.py's /list_fonts).

A browser file dialog can't pick from the Windows Fonts folder (it's a
shell virtual folder that shows up empty there), so the page asks Blender
for the installed fonts instead. Reads each font's display name straight
from its own 'name' table -- no bpy, no third-party dependency, and only a
few hundred bytes per file (header, table directory, name table).
"""

import os
import platform
import struct

# Fonts Blender's text objects can load that also carry a readable 'name'
# table ('.woff' is compressed, '.pfb' has none -- both fall back to their
# file name).
FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".woff", ".pfb")

_cache = None


def _font_dirs():
    dirs = []
    system = platform.system()
    if system == "Windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif system == "Darwin":
        dirs += ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
                 "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts",
                 os.path.expanduser("~/.local/share/fonts"), os.path.expanduser("~/.fonts")]
    return dirs


def _decode(platform_id, raw):
    if platform_id in (0, 3):
        return raw.decode("utf-16-be", errors="ignore")
    return raw.decode("mac_roman", errors="ignore")


def read_font_name(path):
    """Full font name (name ID 4, e.g. "Arial Black"), falling back to
    family + subfamily (IDs 1 + 2). None if the file has no readable 'name'
    table. Prefers English Windows/Unicode records."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12:
                return None
            offset = 0
            if head[:4] == b"ttcf":  # collection: use its first font
                fh.seek(12)
                offset = struct.unpack(">I", fh.read(4))[0]
                fh.seek(offset)
                head = fh.read(12)
            num_tables = struct.unpack(">H", head[4:6])[0]
            if not 0 < num_tables < 200:
                return None
            fh.seek(offset + 12)
            directory = fh.read(16 * num_tables)
            name_off = name_len = None
            for i in range(num_tables):
                tag, _chk, t_off, t_len = struct.unpack(">4sIII", directory[16 * i:16 * i + 16])
                if tag == b"name":
                    name_off, name_len = t_off, t_len
                    break
            if name_off is None or not 6 <= name_len < 2_000_000:
                return None
            fh.seek(name_off)
            table = fh.read(name_len)
    except (OSError, struct.error):
        return None

    try:
        _fmt, count, str_off = struct.unpack(">HHH", table[:6])
        best = {}
        for i in range(count):
            pid, eid, lang, nid, length, off = struct.unpack(">HHHHHH", table[6 + 12 * i:18 + 12 * i])
            if nid not in (1, 2, 4):
                continue
            # Rank: English Windows (3/0x409) > other Windows/Unicode > Mac.
            rank = 0 if (pid == 3 and lang == 0x409) else (1 if pid in (0, 3) else 2)
            if nid in best and best[nid][0] <= rank:
                continue
            raw = table[str_off + off:str_off + off + length]
            text = _decode(pid, raw).strip("\x00 ").strip()
            if text:
                best[nid] = (rank, text)
    except struct.error:
        return None
    if 4 in best:
        return best[4][1]
    if 1 in best:
        sub = best.get(2, (0, ""))[1]
        return best[1][1] + ("" if sub in ("", "Regular") else " " + sub)
    return None


def list_fonts(refresh=False):
    """[{'name': display name, 'path': file path}], sorted by name, one entry
    per name. Cached after the first call -- scanning a full system font
    folder takes a moment -- unless *refresh*."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    seen = {}
    for root_dir in _font_dirs():
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fn in filenames:
                if not fn.lower().endswith(FONT_EXTENSIONS):
                    continue
                path = os.path.join(dirpath, fn)
                name = read_font_name(path) or os.path.splitext(fn)[0]
                seen.setdefault(name.casefold(), {"name": name, "path": path})
    _cache = sorted(seen.values(), key=lambda f: f["name"].casefold())
    return _cache
