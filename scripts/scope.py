"""Scope a copy before running it. Read-only.

    python3 scope.py SOURCE DEST [--out DIR]

SOURCE and DEST are Drive folder IDs, optionally with a sub-path: FOLDER_ID:sub/path

Writes to DIR (default: current directory):
    scope.csv            one row per source file with its status
    todo.txt             plain paths to copy, for `rclone copy --files-from-raw todo.txt`
    duplicates_plan.csv  the duplicate-name files as a plan for execute_plan.py

Statuses:
    to_copy          not in DEST yet
    in_dest          already in DEST at its (planned) path with the same content (skipped)
    differs_in_dest  same path in DEST but different content (skipped; review by hand)
    same_content_elsewhere
                     identical content already in DEST under another path/name, e.g. a
                     'Report.pptx' vs 'report.pptx' case change (skipped; see `found_at`)
    duplicate_name   Drive holds 2+ files with this exact path; rclone would silently copy
                     only one, so these get unique names in the `copy_as` column
                     (oldest -> "(old)", newer -> "(YYYY-MM-DD)")
Also flags copy-protected files and lists shortcuts, which are skipped.
"""
import argparse
import csv
import os
from collections import defaultdict

from common import folder_arg, free_path, is_native, lsjson, md5, same_content, with_tag

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("source")
ap.add_argument("dest")
ap.add_argument("--out", default=".")
args = ap.parse_args()

src_id, src_path = folder_arg(args.source)
dst_id, dst_path = folder_arg(args.dest)

real = lsjson(src_id, src_path, "-M")
followed = lsjson(src_id, src_path, skip_shortcuts=False)
dest = {i["Path"]: i for i in lsjson(dst_id, dst_path)}

shortcuts = sorted({i["Path"] for i in followed} - {i["Path"] for i in real})

by_path = defaultdict(list)
for f in real:
    by_path[f["Path"]].append(f)

dest_by_md5 = {md5(i): p for p, i in dest.items() if md5(i)}


def already_there(f, target):
    """True if `target` in DEST holds this file (same content, or a native at that path)."""
    return target in dest and (is_native(f) or same_content(f, dest[target]))


rows, todo = [], []
for path, group in sorted(by_path.items()):
    group.sort(key=lambda f: f["ModTime"])
    chosen = {path}
    for n, f in enumerate(group):
        protected = str((f.get("Metadata") or {}).get("copy-requires-writer-permission", "")).lower() == "true"
        row = {"path": path, "id": f["ID"], "size": f["Size"], "modified": f["ModTime"][:19],
               "native": "yes" if is_native(f) else "", "copy_protected": "yes" if protected else "",
               "status": "", "copy_as": "", "found_at": ""}
        target = path
        if len(group) > 1:
            # Oldest becomes "(old)"; newer ones get their date (plus time if dates collide).
            target = with_tag(path, "old") if n == 0 else free_path(path, f, chosen)[0]
            if target in dest and not already_there(f, target):
                target = free_path(target, f, chosen | set(dest))[0]
            chosen.add(target)
            row["copy_as"] = target
        if already_there(f, target):
            row.update(status="in_dest", found_at=target)
        elif target == path and path in dest:
            row["status"] = "differs_in_dest"
        elif md5(f) in dest_by_md5:
            row.update(status="same_content_elsewhere", found_at=dest_by_md5[md5(f)])
        elif len(group) > 1:
            row["status"] = "duplicate_name"
        else:
            row["status"] = "to_copy"
            todo.append(path)
        rows.append(row)

os.makedirs(args.out, exist_ok=True)
with open(os.path.join(args.out, "scope.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["path"])
    w.writeheader()
    w.writerows(rows)
with open(os.path.join(args.out, "todo.txt"), "w") as fh:
    fh.write("".join(p + "\n" for p in todo))

join = lambda base, rel: f"{base}/{rel}" if base else rel
with open(os.path.join(args.out, "duplicates_plan.csv"), "w", newline="") as fh:
    fields = ["action", "from_folder", "from_path", "id", "to_folder", "to_path", "note", "size", "modified"]
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    for r in rows:
        if r["status"] == "duplicate_name":
            w.writerow({"action": "copy", "from_folder": src_id, "from_path": join(src_path, r["path"]),
                        "id": r["id"], "to_folder": dst_id, "to_path": join(dst_path, r["copy_as"]),
                        "note": "duplicate name", "size": r["size"], "modified": r["modified"]})

count = lambda s: sum(1 for r in rows if r["status"] == s)
gb = sum(max(int(r["size"]), 0) for r in rows if r["status"] in ("to_copy", "duplicate_name")) / 1e9
print(f"source files:     {len(rows)}")
print(f"  to copy:        {count('to_copy')}")
print(f"  duplicate name: {count('duplicate_name')}  (run: execute_plan.py duplicates_plan.csv)")
print(f"  already in dest:{count('in_dest'):5d}")
print(f"  differs in dest:{count('differs_in_dest'):5d}  (review by hand)")
print(f"  same content elsewhere in dest: {count('same_content_elsewhere')}  (see found_at)")
print(f"  copy-protected: {sum(1 for r in rows if r['copy_protected'])}")
print(f"  to transfer:    {gb:.2f} GB")
print(f"shortcuts skipped: {len(shortcuts)} {shortcuts[:5]}")
print(f"wrote scope.csv, todo.txt, duplicates_plan.csv in {os.path.abspath(args.out)}")
