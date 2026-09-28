"""Plan a merge of messy partial copies into one folder. Read-only: writes a plan CSV.

    python3 merge_plan.py --keep KEEP_ID --donor ID [--donor ID ...]
                          [--source ORIGINAL_ID[:path]] [--route PREFIX=FOLDER_ID[:path]] [--out plan.csv]

KEEP    the folder that stays (e.g. the one people are already working in). Its files are never
        moved or renamed.
DONOR   other copies. Their files that are NOT already in KEEP get MOVED into KEEP at the same
        relative path (a move keeps the file's link, comments and history).
SOURCE  optional original. Files still missing from KEEP afterwards get COPIED from it.
ROUTE   optional: donor paths starting with PREFIX/ go to another folder instead, with the prefix
        stripped (e.g. a stray 'Archive/' subfolder that really belongs in a sibling folder).

Rules:
  * pointer stubs (.gdoc, .gsheet, ... from Drive for Desktop) are skipped
  * identical content (md5) already in any target folder is skipped as a duplicate
  * Google-native files (no md5) are skipped only if one exists at the SAME target path
  * a top-level donor folder named 'X (1)' maps onto 'X' when KEEP has 'X'
  * name clash with different content: incoming file is renamed, older -> '(old)',
    newer -> '(YYYY-MM-DD)'

Review the plan, then run it with execute_plan.py.
"""
import argparse
import csv
import re
from collections import Counter

from common import folder_arg, free_path, is_native, is_pointer, lsjson, md5

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--keep", required=True)
ap.add_argument("--donor", action="append", default=[])
ap.add_argument("--source")
ap.add_argument("--route", action="append", default=[], help="PREFIX=FOLDER_ID[:path]")
ap.add_argument("--out", default="plan.csv")
args = ap.parse_args()


def join(base, rel):
    return f"{base}/{rel}" if base else rel


class Target:
    """A destination folder and what is already (or planned to be) in it."""

    def __init__(self, spec):
        self.id, self.base = folder_arg(spec)
        self.files = {i["Path"]: i for i in lsjson(self.id, self.base)}
        self.taken = set(self.files)

    def tops(self):
        return {p.split("/")[0] for p in self.files}


keep = Target(args.keep)
routes = []
for spec in args.route:
    prefix, _, target = spec.partition("=")
    routes.append((prefix.rstrip("/") + "/", Target(target)))
all_md5 = {md5(i) for t in [keep] + [t for _, t in routes] for i in t.files.values() if md5(i)}


def destination(path):
    for prefix, target in routes:
        if path.startswith(prefix):
            return target, path[len(prefix):]
    top, _, rest = path.partition("/")
    m = re.fullmatch(r"(.+) \(\d+\)", top)
    if rest and m and m.group(1) in keep.tops():
        return keep, m.group(1) + "/" + rest
    return keep, path


def row(action, from_id, from_base, item, target=None, to_rel="", note=""):
    return {"action": action, "from_folder": from_id, "from_path": join(from_base, item["Path"]),
            "id": item["ID"], "to_folder": target.id if target else "",
            "to_path": join(target.base, to_rel) if target else "", "note": note,
            "size": item["Size"], "modified": item["ModTime"][:19]}


plan = []
for spec in args.donor:
    donor_id, donor_base = folder_arg(spec)
    for item in sorted(lsjson(donor_id, donor_base), key=lambda i: i["ModTime"], reverse=True):
        target, rel = destination(item["Path"])
        if is_pointer(item):
            plan.append(row("skip", donor_id, donor_base, item, note="pointer stub"))
        elif md5(item) and md5(item) in all_md5:
            plan.append(row("skip", donor_id, donor_base, item, note="duplicate content"))
        elif is_native(item) and rel in target.taken:
            plan.append(row("skip", donor_id, donor_base, item, note="Google file already at same path"))
        else:
            to_rel, note = free_path(rel, item, target.taken, target.files.get(rel))
            target.taken.add(to_rel)
            if md5(item):
                all_md5.add(md5(item))
            plan.append(row("move", donor_id, donor_base, item, target, to_rel, note))

if args.source:
    src_id, src_base = folder_arg(args.source)
    for item in lsjson(src_id, src_base):
        if (md5(item) and md5(item) in all_md5) or (is_native(item) and item["Path"] in keep.taken):
            continue
        to_rel, note = free_path(item["Path"], item, keep.taken, keep.files.get(item["Path"]))
        keep.taken.add(to_rel)
        if md5(item):
            all_md5.add(md5(item))
        plan.append(row("copy", src_id, src_base, item, keep, to_rel, note or "missing from keep"))

with open(args.out, "w", newline="") as fh:
    fields = ["action", "from_folder", "from_path", "id", "to_folder", "to_path", "note", "size", "modified"]
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(plan)

for (action, note), n in sorted(Counter((r["action"], r["note"].split(" (")[0]) for r in plan).items()):
    print(f"  {action:5s} {n:6d}  {note}")
print(f"wrote {args.out}: {sum(r['action'] in ('move', 'copy') for r in plan)} to do, "
      f"{sum(r['action'] == 'skip' for r in plan)} skipped")
