# Google Drive account-to-account migration playbook (rclone)

A tested, step-by-step way to move a folder tree from an **old Google account to a new one**
(for example when you change organisation) — keeping Google Docs, Sheets and Slides as real
Google files, never downloading anything to your computer, and ending with **one clean copy
with no repeats**.

It comes from a real migration of ~3,000 files / 43 GB (mostly videos and PDFs), including a
clean-up of several half-finished copies made earlier with other tools. Every gotcha listed
below actually happened.

---

## What is and isn't possible

| | Same organisation (same Workspace domain) | Different organisation / personal account |
|---|---|---|
| Transfer ownership (same file moves over) | ✅ keeps everything | ❌ blocked by Google |
| Copy into the new account | ✅ | ✅ — **this playbook** |
| Google Docs stay native | ✅ | ✅ (server-side copy, no conversion) |
| Comments, suggestions, version history | ✅ with transfer | ❌ not carried over by any copy |

Copies are owned by the new account and count against **its** storage. Google limits copying
to about **750 GB per account per day**.

## How it works

1. The **old** account shares the source folder with the **new** account (Viewer is enough).
2. rclone is connected to the **new account only**. Because it can see the shared folder, it asks
   Google to copy each file **inside Drive** ("server-side") — nothing is downloaded, Docs stay
   native, and the new account owns every copy.
3. Everything is scoped and dry-run first, then verified by listing Drive itself.

```
old account ──share──▶ new account can read source
                                   │
         rclone (signed in as NEW) │  server-side copy / move, by folder ID
                                   ▼
                        destination folder (owned by NEW)
```

## Requirements

- [rclone](https://rclone.org) **1.66+** (tested with 1.75.1)
- Python **3.8+** (standard library only) for the helper scripts in [`scripts/`](scripts)
- About 15 minutes of one-time setup

---

## 1. Install rclone

macOS without admin rights (Apple Silicon shown; use `osx-amd64` on Intel):

```bash
curl -O https://downloads.rclone.org/rclone-current-osx-arm64.zip
unzip rclone-current-osx-arm64.zip
mkdir -p ~/.local/bin && cp rclone-v*-osx-arm64/rclone ~/.local/bin/
~/.local/bin/rclone version
```

Verify the zip against the official `SHA256SUMS` in the matching version folder on
downloads.rclone.org (e.g. `https://downloads.rclone.org/v1.75.1/SHA256SUMS`).
Other platforms: <https://rclone.org/install/>.

## 2. Create your own Google OAuth client (do not skip)

rclone's built-in login app is shared by every rclone user in the world. Its quota is exhausted
most of the time (you get `403 Quota exceeded ... rateLimitExceeded`) and Google is retiring it.
Your own client gets its own quota.

Signed into **the account rclone will use** (the new one), at <https://console.cloud.google.com/>:

1. Create a project (e.g. `drive-rclone`).
2. **APIs & Services → Library → Google Drive API → Enable.**
   Forgetting this gives `403 SERVICE_DISABLED ... Google Drive API has not been used in project`.
   After enabling, allow 1–2 minutes to propagate.
3. **OAuth consent screen → User type: Internal** (Workspace accounts). Internal apps don't get
   the 7-day token expiry that unverified External apps have. Only accounts in that same
   organisation can then sign in with it.
4. **Credentials → Create credentials → OAuth client ID → Desktop app.** Note the client ID and
   secret.

Treat the client secret like a password: don't paste it into chats, tickets or commits. If it
leaks, use **Reset secret** on the credential and reconnect.

## 3. Connect rclone to the new account

```bash
rclone config create gdrive drive scope=drive client_id=YOUR_CLIENT_ID client_secret=YOUR_CLIENT_SECRET
```

A browser tab opens — sign in as the **new** account and allow access. The browser's "Success!"
page appears *before* rclone finishes; the command is only done when the terminal returns without
an error (an `invalid_client` error means the secret was wrong).

<details>
<summary>macOS tip: enter the secret in a hidden popup instead of on the command line</summary>

```bash
S=$(osascript -e 'text returned of (display dialog "Paste the client secret:" default answer "" with hidden answer)') \
  && rclone config create gdrive drive scope=drive client_id=YOUR_CLIENT_ID client_secret="$S" >/dev/null; unset S
```
</details>

Check which account rclone is signed in as (rclone's `userinfo` doesn't support Drive):

```bash
TOKEN=$(rclone config dump | python3 -c "import json,sys; print(json.loads(json.load(sys.stdin)['gdrive']['token'])['access_token'])")
curl -s -H "Authorization: Bearer $TOKEN" "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)"
```

### Addressing folders

Always address folders **by ID** (the part after `/folders/` in the URL) using an rclone
connection string:

```
gdrive,root_folder_id=FOLDER_ID:optional/sub/path
```

Drive happily holds several sibling folders with the *same name* — paths alone are ambiguous.
The helper scripts read the remote name from `RCLONE_REMOTE` (default `gdrive`).

---

## 4. Scope first (read-only)

```bash
cd scripts
python3 scope.py OLD_FOLDER_ID NEW_FOLDER_ID --out ../work
```

It lists both sides and reports how many files are to copy, already there, different, stored
twice under one name, copy-protected, or shortcuts. It writes `scope.csv` (review it),
`todo.txt` and `duplicates_plan.csv`.

- **Copy-protected** files ("viewers can't download or copy") fail with 403 — give the new
  account Editor access or have the owner lift the restriction.
- **Shortcuts** are skipped; decide separately whether you want their targets.

## 5. Dry run, then copy

```bash
SRC="gdrive,root_folder_id=OLD_FOLDER_ID:"
DST="gdrive,root_folder_id=NEW_FOLDER_ID:"

# dry run: prints what would be copied, changes nothing
rclone copy "$SRC" "$DST" --files-from-raw ../work/todo.txt --ignore-existing \
  --server-side-across-configs --drive-skip-shortcuts --retries 1 --dry-run

# the real copy
rclone copy "$SRC" "$DST" --files-from-raw ../work/todo.txt --ignore-existing \
  --server-side-across-configs --drive-skip-shortcuts --transfers 8 --checkers 8 \
  --log-file ../work/copy.log --log-level INFO --stats 30s --stats-one-line
```

Then copy the files that share a name with a sibling (rclone can't, see gotchas), under unique
names — test one first:

```bash
python3 execute_plan.py ../work/duplicates_plan.csv --dry-run
python3 execute_plan.py ../work/duplicates_plan.csv --limit 1
python3 execute_plan.py ../work/duplicates_plan.csv
```

Naming rule for same-name files: the **oldest** becomes `Name (old)`, newer ones get their
date, `Name (2025-02-26)`, plus the time if two share a date. Existing files in the destination
are never renamed.

## 6. Verify against Drive

```bash
python3 verify.py --source OLD_FOLDER_ID --dest NEW_FOLDER_ID
```

Every source file must be in the destination — by content (md5), or for Google-native files
(which have no md5) at the same path. Don't rely on rclone's own "OK" messages alone.

## 7. (Optional) Merge messy partial copies into one

If earlier attempts left several half-copies (e.g. four folders all called `Reports`), pick the
one people already work in as **keep** and fold the others in:

```bash
python3 merge_plan.py --keep KEEP_ID --donor COPY2_ID --donor COPY3_ID \
  --source OLD_FOLDER_ID:Reports --out ../work/merge.csv
# stray subfolders that belong elsewhere can be routed: --route 'Archive=ARCHIVE_FOLDER_ID'
python3 execute_plan.py ../work/merge.csv --dry-run
python3 execute_plan.py ../work/merge.csv --limit 1
python3 execute_plan.py ../work/merge.csv --workers 4
```

Unique files are **moved** (keeps their links, comments and history); exact duplicates and
Drive-for-Desktop pointer stubs (`.gdoc`, `.gsheet`, ...) are left behind; anything still
missing is copied from the original. `execute_plan.py` stops at the first operation that isn't
server-side, skips targets that already exist (safe to interrupt and re-run), and creates all
target folders up front when running in parallel.

To move a whole top-level copy into its final home, the same tool works with the final folder
as `--keep` and the top-level copy as the only `--donor`.

## 8. Clean up

Right before removing anything, re-check that the leftovers hold nothing unique — people may
have added files since your last listing:

```bash
python3 verify.py --dest FINAL_ID --dest FINAL_SIBLING_ID --leftover OLD_COPY_ID --leftover OLD_COPY2_ID
```

Then move the leftovers to **Trash** in the Drive UI (recoverable for 30 days). Storage is only
freed once Trash is emptied.

## Importing a single file someone else owns

The new account needs access first (share it with the new account). Then a server-side copy
keeps it native, with all tabs:

```bash
rclone backend copyid gdrive: FILE_ID "gdrive,root_folder_id=DEST_FOLDER_ID:New name" --server-side-across-configs
```

It's a snapshot: later edits to the original won't appear in the copy.

---

## Gotchas we hit (and how the scripts handle them)

1. **Shared rclone client is throttled** → own OAuth client (step 2), and **enable the Drive API**
   in that project.
2. **`--files-from` trims spaces.** File names ending in a space were silently skipped. Use
   `--files-from-raw`.
3. **Drive allows two files with the same name in one folder; rclone doesn't.** It copies one,
   logs `Duplicate object found`, and skips the rest. `scope.py` detects these and plans unique
   names.
4. **`rclone backend copyid` needs the remote prefix on the destination.** With a bare path like
   `Folder/file` it treats the path as **local**, downloads the file to your computer, and still
   exits successfully. `execute_plan.py` always uses the full `remote,root_folder_id=...:path`
   form and checks that the log says `(server-side copy)`.
5. **Never trust "OK" alone.** After every run, list Drive and compare. Our first scan also
   over-reported what was "already copied"; the independent `rclone check` caught it before
   anything ran.
6. **Google-native files have no checksum.** Compare them by path, not by name anywhere —
   two different Sheets with the same name nearly got merged into one.
7. **Cross-config operations need `--server-side-across-configs`**, otherwise rclone falls back
   to download + upload (and converts Docs to Office files on the way).
8. **Parallel moves can create duplicate folders** if two workers create the same folder at
   once → create target folders first, then run workers.
9. **A dry run adds up retries.** When an attempt fails and is retried, a dry run's counts
   include both attempts; run with `--retries 1` for an exact preview.
10. **Case-only renames** (`Report.pptx` vs `report.pptx`) look like different files by path;
    `scope.py` reports these as "same content elsewhere".
11. **Drive for Desktop leftovers** — `.gdoc`/`.gsheet` files are 177-byte pointers, not
    documents. Don't migrate them.
12. **The destination can change under you** if others are working in it. Re-list right before
    you move or trash anything.

## Alternatives we considered

- **Google Apps Script** (`makeCopy` from the new account): simple and keeps files native, but
  scripts stop after ~6 minutes, so big trees need repeated runs. Fine for small folders.
- **Google Takeout**: only files you own, as one big archive; nothing lands back in Drive.
- **Drive for Desktop**: Google files appear as `.gdoc` pointer stubs, not real files.
- **rclone with two remotes (old + new)**: works, but downloads and re-uploads everything and
  converts Docs to `.docx`/`.xlsx` and back. One remote (the new account) + sharing avoids this.

## Safety principles

- Read-only scoping and dry runs before any change; test on one file before a batch.
- Server-side operations only, checked on every call.
- Moves over copies when merging (no extra storage, links keep working).
- Verify by listing Drive, independently of the tool that did the work.
- Trash, don't delete — and re-verify right before trashing.
