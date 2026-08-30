"""Filesystem access.

Reads are open; every mutation goes through ``guard_write_path``, which fences
writes to the configured roots and refuses protected system directories.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..appindex import index
from ..log import get
from ..security import Risk, guard_write_path
from .registry import tool

log = get("jarvis.tools.files")

_KNOWN = {
    "desktop": "~/Desktop", "documents": "~/Documents", "downloads": "~/Downloads",
    "pictures": "~/Pictures", "videos": "~/Videos", "music": "~/Music",
    "home": "~", "projects": "~/projects",
}

_TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv",
    ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".xml",
    ".sh", ".ps1", ".bat", ".sql", ".java", ".c", ".cpp", ".h", ".rs", ".go",
}


def _resolve(path: str) -> Path:
    """Expand shortcuts like 'desktop' and '~' into a real path."""
    key = path.strip().casefold()
    if key in _KNOWN:
        path = _KNOWN[key]
    return Path(os.path.expandvars(path)).expanduser()


@tool(
    risk=Risk.SAFE,
    params={"path": "Folder to list. Accepts shortcuts like 'desktop' or 'downloads'."},
    summary=lambda a: f"List {a.get('path', '?')}",
    tags=["files"],
)
def list_dir(path: str = "home", limit: int = 60) -> dict:
    """List the contents of a folder."""
    target = _resolve(path)
    if not target.is_dir():
        return {"ok": False, "error": f"{target} is not a folder."}

    items = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.casefold())):
        try:
            stat = child.stat()
            items.append({
                "name": child.name,
                "type": "folder" if child.is_dir() else "file",
                "size_mb": round(stat.st_size / 1e6, 2) if child.is_file() else None,
            })
        except OSError:
            continue
        if len(items) >= limit:
            break
    return {"path": str(target), "count": len(items), "items": items}


@tool(
    risk=Risk.SAFE,
    params={
        "query": "Filename or fragment to search for.",
        "root": "Folder to search under. Defaults to your user folder.",
    },
    summary=lambda a: f"Search for {a.get('query', '?')!r}",
    tags=["files"],
)
def search_files(query: str, root: str = "home", limit: int = 25) -> dict:
    """Find files and folders whose name contains the query."""
    base = _resolve(root)
    if not base.is_dir():
        return {"ok": False, "error": f"{base} is not a folder."}

    needle = query.casefold()
    skip = {"node_modules", ".git", "__pycache__", ".venv", "AppData", "$Recycle.Bin"}
    hits: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(base, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames + dirnames:
            if needle in name.casefold():
                full = Path(dirpath) / name
                hits.append({"name": name, "path": str(full), "type": "folder" if full.is_dir() else "file"})
                if len(hits) >= limit:
                    return {"count": len(hits), "results": hits, "truncated": True}
    return {"count": len(hits), "results": hits, "truncated": False}


@tool(
    risk=Risk.SAFE,
    params={"path": "File to read."},
    summary=lambda a: f"Read {a.get('path', '?')}",
    tags=["files"],
)
def read_text_file(path: str, max_chars: int = 6000) -> dict:
    """Read a text file's contents."""
    target = _resolve(path)
    if not target.is_file():
        return {"ok": False, "error": f"{target} is not a file."}
    if target.suffix.casefold() not in _TEXT_SUFFIXES:
        return {"ok": False, "error": f"{target.suffix} is not a readable text format."}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "path": str(target),
        "content": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


@tool(
    risk=Risk.HIGH,
    params={"path": "File to write.", "content": "Text to write.",
            "append": "Append instead of overwriting."},
    summary=lambda a: f"Write to {a.get('path', '?')}",
    tags=["files"],
    precheck=lambda a: guard_write_path(_resolve(a.get("path", ""))),
)
def write_text_file(path: str, content: str, append: bool = False) -> dict:
    """Create or overwrite a text file."""
    target = guard_write_path(_resolve(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a" if append else "w", encoding="utf-8") as handle:
        handle.write(content)
    return {"path": str(target), "bytes": len(content.encode("utf-8"))}


@tool(
    risk=Risk.SAFE,
    params={"path": "Folder to create."},
    summary=lambda a: f"Create folder {a.get('path', '?')}",
    tags=["files"],
)
def create_folder(path: str) -> dict:
    """Create a folder, including any missing parents."""
    target = guard_write_path(_resolve(path))
    target.mkdir(parents=True, exist_ok=True)
    return {"path": str(target)}


@tool(
    risk=Risk.HIGH,
    params={"source": "Path to move.", "destination": "Where to move it to."},
    summary=lambda a: f"Move {a.get('source', '?')} to {a.get('destination', '?')}",
    tags=["files"],
    precheck=lambda a: (
        guard_write_path(_resolve(a.get("source", ""))),
        guard_write_path(_resolve(a.get("destination", ""))),
    ) and None,
)
def move_path(source: str, destination: str) -> dict:
    """Move or rename a file or folder."""
    src = guard_write_path(_resolve(source))
    dst = guard_write_path(_resolve(destination))
    if not src.exists():
        return {"ok": False, "error": f"{src} does not exist."}
    if dst.is_dir():
        dst = dst / src.name
    shutil.move(str(src), str(dst))
    return {"from": str(src), "to": str(dst)}


@tool(
    risk=Risk.HIGH,
    params={"path": "File or folder to send to the Recycle Bin."},
    summary=lambda a: f"Delete {a.get('path', '?')}",
    tags=["files"],
    precheck=lambda a: guard_write_path(_resolve(a.get("path", ""))),
)
def delete_path(path: str) -> dict:
    """Send a file or folder to the Recycle Bin.

    Deletion is recoverable by design -- nothing here bypasses the Recycle Bin.
    """
    target = guard_write_path(_resolve(path))
    if not target.exists():
        return {"ok": False, "error": f"{target} does not exist."}

    # SHFileOperation with ALLOWUNDO is what Explorer itself uses, so the item
    # lands in the Recycle Bin rather than vanishing.
    from win32com.shell import shell, shellcon

    result, aborted = shell.SHFileOperation((
        0,
        shellcon.FO_DELETE,
        str(target),
        None,
        shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT,
        None,
        None,
    ))
    if result != 0 or aborted:
        return {"ok": False, "error": f"Delete failed (code {result})."}
    return {"deleted": str(target), "note": "Sent to the Recycle Bin."}


@tool(
    risk=Risk.MODERATE,
    params={"path": "File, folder, or URL to open in its default application."},
    summary=lambda a: f"Open {a.get('path', '?')}",
    tags=["files"],
)
def open_path(path: str) -> dict:
    """Open a file or folder with its default application."""
    target = _resolve(path)
    if not target.exists():
        return {"ok": False, "error": f"{target} does not exist."}
    index.launch_path(target)
    return {"opened": str(target)}
