# Custom Campaign Manager

Run the complete launcher normally, or use:

    python -m sc2_campaign_launcher_linux --ccm-only

On Windows, build the separate portable application with:

    python tools/build_release.py --ccm-only

The result is dist/SC2CampaignManager-Windows-x86_64.zip. Extract the entire ZIP
and run SC2CampaignManager.exe. Both interfaces share preferences, imported
campaigns, and installation records. Close one before opening the other.

## Import and activation

Drop a ZIP or campaign folder onto the My campaigns tab. ZIP selection and folder
selection are also available. A package needs exactly one metadata.txt using
the CCM title, campaign, author, version, and desc fields. The supported slots
are WoL, HotS, LotV, and Nova Covert Ops. The metadata file's directory supplies
the maps; SC2Mod files and unpacked SC2Mod directories elsewhere in the package
are installed under Mods. LotV prologue files are assigned to the prologue slot.

Imported copies live under Maps/CustomCampaigns. Load activates a package.
A loaded campaign's card offers Revert to base, which restores its campaign
slot while leaving the other slots alone. The custom package remains imported
and can be loaded again. Play activates the selected campaign first, then opens
StarCraft II. Only imported custom campaigns have cards.

Only one replacement per slot can be active. Other slots can remain active
when their shared mod versions agree. Conflicting dependencies stop the switch.

Campaign packages stay intact during activation. The launcher records and
backs up the files it replaces, including pre-existing mods. It refuses to
overwrite edits or newly added maps in an active slot. Revert a loaded
campaign to base before removing its stored copy. Unload campaigns in the original CCM
before handing their slots to this launcher.

## Base campaigns and saves

Revert to base restores the loose files that were present before activation.
If an earlier launcher build installed an extracted base package, reverting
uses that package instead. The launcher checks its game build and languages,
and refreshes it from installed CASC data when needed. An update during
extraction stops the operation before changing the active campaign.

The installed game supplies its normal assets and dependencies. No game maps
or assets are included in the downloadable launcher. Windows includes CascLib
3.0 with its license; Linux extraction needs a compatible libcasc 3.x. Set
SC2CL_CASCLIB to an absolute library path if it is outside the system loader's
search directories. Extraction has been checked on Windows and Linux for all
four campaigns. Import and restoring original files do not require CascLib.

Cards use artwork read from the installed game and cached in the per-user data
directory. Windows bundles include the image decoder. Source installations can
enable artwork with:

    python -m pip install ".[artwork]"

Without CascLib or the decoder, cards keep their text covers and package import
still works.

On Windows, Play opens StarCraft II through the installed Battle.net client so
Battle.net can handle automatic sign-in. Sign in to Battle.net first and make
sure it uses the same SC2 installation selected in Settings. The launcher finds
Battle.net through its registered Windows command or its default install folder.
If it cannot find Battle.net, use Load and start SC2 from Battle.net yourself.
Linux continues to use the configured Wine or Proton runner.

Start a new campaign, continue, or select a saved game inside SC2. The manager
no longer guesses which mod owns a campaign save from its filename. Saves and
banks remain shared with the game. Account backups provide an explicit way to
restore progress; automatic per-campaign profiles are not implemented.

The launch monitor waits up to two minutes for SC2 to start and then tracks the
game until it exits. On Linux, this does not depend on Wine or Proton exiting or
closing its output pipe. Campaign controls stay disabled during that time, even if
the manager is closed and reopened. Leaving Battle.net open does not keep the
campaign controls locked. If startup times out, check Battle.net for sign-in or
updates before retrying. The selected campaign stays loaded.

Linux process detection ignores exited zombie processes. After a timeout or game
exit, the launch result releases the controls independently of the output reader.
The reader continues collecting bounded logs until the runner and its descendants
close their output, so a slow initialization does not lose its pipe. It may remain
in the background after the launch result is shown. A late game start is still
possible; close the stalled runner before retrying. File operations check for a
running game before modifying campaign or progress files.

On Linux, Recover launch controls is available while a launch is being tracked.
Use it for a failed launch left behind by an older version. Close the stalled
Wine or Proton launch first, then confirm recovery. Recovery refuses while SC2
is detected, keeps campaign files and logs, and prevents the old launch record
from disabling controls again after reopening. It does not terminate processes;
a runner left open may still start the game later. New launch attempts use
separate log and result files so an old monitor cannot overwrite their records.

## Saved progress

Load, Play, and Revert to base back up the selected account’s Saves and Banks
before changing campaigns. Playing an already loaded campaign does not create
a redundant backup. If one account is detected, it is selected automatically;
multiple accounts require a choice. The choice is remembered. Before switching
Battle.net accounts, choose the corresponding account in Saved progress.
If no account data exists yet, campaign loading remains available.

Saved progress lets you choose another Saves folder, create a manual backup,
or restore a dated backup. Backups cover the whole account, including progress
for other campaigns. The restore dialog asks for confirmation and shows the
account and selected backup. Restoring backs up the current files first, then
replaces files contained in the chosen backup. Files created after that backup
remain in place. Restoring does not select a campaign or verify save compatibility.

Backups are verified before restoring. Cancellation rolls back unfinished
changes. A recovery record survives an abrupt exit; open Saved progress and
use Recover restore to return to the files present before the interrupted
restore. Changed files or damaged recovery backups stop recovery so they can
be preserved. Close SC2 before backing up, restoring, or recovering progress.

Backups are stored under save-backups in the per-user launcher data directory,
separately from imported campaign packages. Removing a campaign keeps its
account backups. Backups are retained without automatic deletion and take disk
space. Limits are 10,000 files and 16 GiB per account backup. An unsuccessful
backup stops the campaign switch.

## Author bank metadata

Imported campaigns are scanned for bank references. On a campaign card, choose
More > Scan and review bank references to rescan an existing import and inspect
the results. When the campaign library opens, older imports without a scan report
are scanned automatically in a worker. Other file operations and pending recovery
take precedence. Existing reports, including reports with no references or with
warnings, are kept. Reviewed local lists are never filled in or overwritten by
automatic scans. Cancelling keeps completed reports and leaves missing ones for
the next launch. A failed scan is attempted once per session; the More menu can
retry it manually.

The reader checks BankList.xml, DocumentInfo bank preloads, and
Galaxy scripts in packed and unpacked maps and mods. It recognizes literal bank
names and simple global string constants. Runtime names and missing dependencies
are reported for review. It does not read Blizzard's external campaign archives.

Each reference includes its source files. The review window matches names to
existing bank paths in the selected account and shows references shared by other
scanned imports. Shared references are not proof of exclusive ownership. Missing
matches can mean the bank has not been created yet or belongs to another account.
Quoted, unsafe, or unsupported names are reported instead of guessed.

Use the local list field to record reviewed paths. Lists are kept separately for
each account and imported package; changed package contents mark an old list as
needing review. Scanning and saving local lists never move or modify bank files.
Local lists do not yet control backups or switching. Author declarations below
continue to control the existing campaign-specific backup feature.

Scan reports are stored in bank-scan.json beside the imported package.json,
outside its payload. Local lists live under the launcher's CCM state directory
in bank-lists. Neither is installed into campaign maps or exported to authors.
Encrypted archives, unsupported compression, oversized scripts, and unreadable
members produce warnings. An empty result must not be treated as proof that the
campaign uses no banks.

Authors can include campaign-progress.json beside metadata.txt in the unpacked
campaign folder and campaign ZIP. The file declares exact bank paths relative
to an account’s Banks folder:

```json
{
  "format": 1,
  "banks": [
    "CampaignProgress.SC2Bank",
    "1-S2-1-1234567/CampaignProgress.SC2Bank"
  ]
}
```

The first example is a local bank directly under Banks. The second includes an
author-ID folder. List only paths that the campaign actually uses. Published
banks may use different author-ID folders in different regions; include the
applicable exact paths. Paths use forward slashes. Absolute paths, traversal,
wildcards, duplicate paths, and files other than SC2Bank are rejected. Limits
are format 1 and 256 bank paths. An empty list declares no banks. The file
contains paths only, never bank contents or player account paths.

More > Author tools: bank metadata opens a small author editor. Choose an unpacked campaign source
folder containing metadata.txt, then enter one bank path per line. Add existing
bank files lets you choose an account’s Banks folder and select files to add.
Save writes campaign-progress.json beside metadata.txt. Editing inside the
selected SC2 installation is refused. Include the file when packaging the ZIP.
To add metadata to a version already imported, revert it, remove its imported
copy, and import the updated ZIP. Existing versions without the file still work.

Saved progress offers Campaign banks scopes for imported campaigns with a bank
list. A scoped backup and restore touch only the declared banks found on disk.
SC2 saves stay in the Whole account scope. Before a campaign switch the manager
keeps the whole-account backup and also creates bank-only backups for the
relevant declared campaigns. Missing bank files are skipped. New files are
preserved when restoring. Automatic bank swapping or clearing is not enabled.

The declarations are supplied by the author; the manager does not prove
ownership from a bank’s contents. It validates paths and checks for overlaps
among imported campaigns. Overlapping claims from different campaigns stop
bank-only backup and restore through Saved progress. Automatic switches keep
the whole-account backup and skip conflicting bank-only scopes. Different
versions of the same author/title/campaign slot share an ownership identity.
Conflicts with campaigns that have no declaration cannot be detected.

## Recovery and storage

File operations run in a worker. Cancellation restores the previous files.
Recovery also cleans unfinished copies left by an abruptly terminated process.
A persistent journal allows the next operation to recover an interrupted
switch. An operation lock prevents another launcher from switching the same library
at the same time. Backups and records live in the launcher's per-user data directory
under ccm. Keep that directory until all custom campaigns have been reverted to base.

New switches record SHA-256 hashes of both sides of each change. Recovery verifies
all rollback backups and existing targets before removing any campaign files.
An edited target or damaged backup stops recovery and preserves the journal.
Original loose-file backups also retain hashes for subsequent Revert operations;
copies are checked before replacing their destinations.

Completed installations from older builds remain usable: their original backups
acquire hashes of their current bytes on the next successful switch. This cannot
detect corruption that occurred before those hashes existed. Pending journals
from older builds have no integrity data and now stop for manual review. Preserve
the complete ccm state directory and affected game files; do not delete pending.json
or retry with fabricated hashes. Review and restore the original files from a
separate copy before resetting the installation record.

If an active map or mod was edited outside the launcher, preserve the edit
separately and restore the expected file before switching. If a recovery backup
is missing, the launcher stops instead of discarding the surviving files.

## Catalogs and updates

Campaign sorting supports name, author, author update date, and map count.
Catalog updated_at values use an ISO 8601 date or a timestamp with a timezone.
Imported campaigns can declare the same optional field in metadata.txt:

```text
updated_at=2026-10-25
```

A timestamp such as `2026-10-25T18:30:00Z` is also accepted. This is the author's
update date; installation times and file modification times are not used.
Last updated sorts newest first, with alphabetical ordering for equal dates.
Missing dates sort last. Invalid dates stop the import with an error.
Previously imported copies without a stored date remain undated. To add one,
revert and remove the imported copy, then import the package with updated metadata.

Map count includes SC2Map containers, which may include
launcher or supporting maps; it is not a declared mission count.

Discrete campaigns with launcher: false offer a mission picker. CCM campaigns
use the normal campaign menu or campaign saves instead.

Settings checks the public fork at RogerRoger29/SC2_Campaign_Launcher_Linux.
No GitHub login is required.
Linux checks development commits; Windows checks published releases.
No update is downloaded or applied without the user's action.

Hosted CCM ZIP catalogs, publisher uploads, save/bank profiles, and SC1/WC3
support are not implemented in this version.
