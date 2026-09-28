"""Shared helpers for the Drive migration scripts (Python 3.8+, standard library only).

Every folder is addressed by its Drive folder ID through an rclone connection string:
    <remote>,root_folder_id=<FOLDER_ID>:<path>
so sibling folders with identical names never get confused.

Configure with environment variables:
    RCLONE         path to the rclone binary       (default: "rclone", then ~/.local/bin/rclone)
    RCLONE_REMOTE  name of your rclone Drive remote (default: "gdrive")
"""
import json
import os
import shutil
import subprocess
import sys

RCLONE = os.environ.get("RCLONE") or shutil.which("rclone") or os.path.expanduser("~/.local/bin/rclone")
REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")

# Tiny stub files that Drive for Desktop creates in place of Google Docs/Sheets/etc.
# They are pointers, not documents, and are never worth migrating.
POINTER_EXT = {".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gvid", ".gsite",
               ".gmap", ".gjam", ".glink", ".gtable", ".gscript"}


def remote(folder_id, path=""):
    return f"{REMOTE},root_folder_id={folder_id}:{path}"


def rclone(*args, check=True):
    res = subprocess.run([RCLONE, *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        sys.exit(f"rclone {' '.join(args[:2])} failed:\n{res.stderr[-800:]}")
    return res


def lsjson(folder_id, path="", *flags, skip_shortcuts=True):
    """Recursive listing of files (with md5 hashes) under a folder."""
    extra = ["--drive-skip-shortcuts"] if skip_shortcuts else []
    res = rclone("lsjson", "-R", "--hash", "--files-only", *extra, *flags, remote(folder_id, path))
    return json.loads(res.stdout or "[]")


def folder_arg(value):
    """Parse 'FOLDER_ID' or 'FOLDER_ID:sub/path' into (id, path)."""
    folder_id, _, path = value.partition(":")
    return folder_id, path


def md5(item):
    return (item.get("Hashes") or {}).get("md5") or ""


def is_native(item):
    """Google Docs/Sheets/Slides have no md5 and report size -1."""
    return not md5(item) and item.get("Size", 0) == -1


def is_pointer(item):
    return os.path.splitext(item["Name"])[1].lower() in POINTER_EXT and 0 <= item["Size"] <= 2048


def same_content(a, b):
    if md5(a) and md5(b):
        return md5(a) == md5(b)
    return a.get("Size") == b.get("Size") and a.get("Size", -1) >= 0


def split_name(name):
    base, dot, ext = name.rpartition(".")
    if dot and base and " " not in ext and 1 <= len(ext) <= 5:
        return base, "." + ext
    return name, ""


def with_tag(path, tag):
    """'a/b/Report.pdf' + 'old' -> 'a/b/Report (old).pdf'"""
    folder, _, name = path.rpartition("/")
    base, ext = split_name(name)
    return (folder + "/" if folder else "") + f"{base} ({tag}){ext}"


def free_path(path, item, taken, existing=None):
    """Pick an unused target path, applying the naming rule on a clash:
    the older file gets '(old)', a newer one gets '(YYYY-MM-DD)', and '(YYYY-MM-DD HH-MM)'
    if the date alone is taken too. Files already at the target are never renamed."""
    if path not in taken:
        return path, ""
    older = existing is not None and item["ModTime"] < existing["ModTime"]
    tags = (["old"] if older else []) + [item["ModTime"][:10], item["ModTime"][:16].replace(":", "-")]
    for tag in tags:
        if with_tag(path, tag) not in taken:
            return with_tag(path, tag), f"renamed ({tag})"
    sys.exit(f"no free name for {path}")
