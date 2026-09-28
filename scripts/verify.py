"""Check a migration by listing Drive itself. Read-only.

    python3 verify.py --source ORIGINAL --dest FINAL [--dest FINAL2 ...] [--leftover ID ...]

Each argument is FOLDER_ID or FOLDER_ID:sub/path.

  * every file in SOURCE must be in one of the DEST folders: same content (md5), or for
    Google-native files (which have no md5) a native file at the same relative path
  * every file in each LEFTOVER folder must already be in DEST (pointer stubs ignored) -
    run this right before you trash leftovers; exits non-zero if anything is unique
"""
import argparse
import sys

from common import folder_arg, is_native, is_pointer, lsjson, md5

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--source")
ap.add_argument("--dest", action="append", required=True)
ap.add_argument("--leftover", action="append", default=[])
args = ap.parse_args()

dest_files = [i for spec in args.dest for i in lsjson(*folder_arg(spec))]
dest_md5 = {md5(i) for i in dest_files if md5(i)}
dest_native_paths = {i["Path"] for i in dest_files if is_native(i)}


def present(item):
    return (md5(item) and md5(item) in dest_md5) or (is_native(item) and item["Path"] in dest_native_paths)


problems = 0
if args.source:
    src = lsjson(*folder_arg(args.source))
    missing = [i["Path"] for i in src if not present(i)]
    problems += len(missing)
    print(f"source: {len(src)} files, missing from dest: {len(missing)}")
    for p in missing[:20]:
        print("   MISSING", p)

for spec in args.leftover:
    items = lsjson(*folder_arg(spec))
    unique = [i["Path"] for i in items if not is_pointer(i) and not present(i)]
    problems += len(unique)
    print(f"leftover {spec}: {len(items)} files, NOT already in dest: {len(unique)}"
          + ("  -> safe to trash" if not unique else ""))
    for p in unique[:20]:
        print("   UNIQUE", p)

sys.exit(1 if problems else 0)
