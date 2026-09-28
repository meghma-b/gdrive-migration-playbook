"""Carry out a plan CSV from merge_plan.py or scope.py: server-side moves and copies only.

    python3 execute_plan.py PLAN.csv [--dry-run] [--limit N] [--workers N]

  --dry-run    print what would happen, change nothing
  --limit N    only the first N move rows and first N copy rows (test on a few files first!)
  --workers N  run N operations in parallel (all target folders are created first, one at a
               time, so parallel workers can never create duplicate folders)

Safety:
  * every operation must be reported by rclone as server-side AND the target must then exist
    in Drive, otherwise the run stops (rclone can print "OK" while doing something else)
  * targets that already exist are skipped, so re-running after an interruption is safe
  * results are appended to execute.log next to the plan
"""
import argparse
import csv
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from common import rclone, remote

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("plan")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--limit", type=int)
ap.add_argument("--workers", type=int, default=1)
args = ap.parse_args()

plan = list(csv.DictReader(open(args.plan)))
rows = [r for r in plan if r["action"] == "move"][:args.limit] + \
       [r for r in plan if r["action"] == "copy"][:args.limit]


def exists(folder, path):
    res = rclone("lsjson", "--stat", remote(folder, path), check=False)
    return res.returncode == 0 and '"Path"' in res.stdout


if args.dry_run:
    for r in rows:
        print(f"{r['action']:4s} {r['from_path']}  ->  [{r['to_folder'][:8]}] {r['to_path']}  {r['note']}")
    sys.exit(f"dry run: {len(rows)} operations, nothing changed")

if args.workers > 1:
    for folder, d in sorted({(r["to_folder"], r["to_path"].rpartition("/")[0]) for r in rows if "/" in r["to_path"]}):
        rclone("mkdir", remote(folder, d))

lock, stop = threading.Lock(), threading.Event()
counts = {"done": 0, "skipped": 0}
log = open(os.path.join(os.path.dirname(os.path.abspath(args.plan)), "execute.log"), "a", buffering=1)


def run_one(r):
    if stop.is_set():
        return
    if exists(r["to_folder"], r["to_path"]):
        with lock:
            counts["skipped"] += 1
            log.write(f"SKIP exists {r['to_path']}\n")
        return
    if r["action"] == "move":
        res = rclone("moveto", remote(r["from_folder"], r["from_path"]), remote(r["to_folder"], r["to_path"]),
                     "--server-side-across-configs", "-v", check=False)
        ok = res.returncode == 0 and "(server-side)" in res.stderr
    else:
        # The destination MUST carry the remote prefix: a bare path is treated as a local path,
        # and rclone would download the file to this computer while still reporting success.
        res = rclone("backend", "copyid", remote(r["to_folder"]), r["id"], remote(r["to_folder"], r["to_path"]),
                     "--server-side-across-configs", "-v", check=False)
        ok = res.returncode == 0 and "(server-side copy)" in res.stderr
    with lock:
        if not ok or not exists(r["to_folder"], r["to_path"]):
            stop.set()
            log.write(f"FAIL {r['action']} {r['from_path']}\n{res.stderr[-800:]}\n")
            return
        counts["done"] += 1
        log.write(f"OK   {r['action']} {r['from_path']} -> [{r['to_folder'][:8]}] {r['to_path']}\n")


with ThreadPoolExecutor(args.workers) as pool:
    list(pool.map(run_one, rows))
log.close()
print(f"done: {counts['done']}, skipped (already there): {counts['skipped']}, of {len(rows)}")
if stop.is_set():
    sys.exit("STOPPED on a failure - see FAIL lines in execute.log")
