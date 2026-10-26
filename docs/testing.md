# CCM development checks

## Bank scanner, October 25, 2026

The original Hyperion Crew 2.03 and Enhanced Swarm 1.55 ZIPs were imported into
temporary game roots. Hyperion Crew produced 37 bank references from 97 documents;
Enhanced Swarm produced 11 from 142. Both referenced TCampaign. Five Hyperion
references and seven Enhanced Swarm references matched filenames in the selected
local account. Filename matches are not ownership claims.

All supported archive members were read without MPQ errors. Reports retained
warnings about runtime names, external dependencies, and quoted bank names.
The source ZIP hashes stayed unchanged. Local bank lists were saved and reloaded
against temporary account fixtures, and those bank files remained unchanged.
The real account was used only to enumerate filenames; its bank contents were
not opened or modified by the scanner.

Automated cases cover packed and unpacked content, current SC2 MPQ headers,
sector compression, decompression limits, invalid archives and XML, cancellation,
changed payloads, shared references, account separation, stale local lists, and
the review window. No real bank switching is performed by this feature.

The automated suite covers imports, map/mod conflicts, switching between
campaign slots, packed and unpacked maps, cancellation, external edits,
recovery after process termination, and refreshing base maps after a game
patch.

Hyperion Crew 2.03 was checked using its original CCM ZIP in a temporary game
directory. The import contained 30 maps and 180 files. Two load/unload cycles
verified every installed file against its stored hash, restored the original
map, and preserved an unrelated mod. Removing the unloaded package left no
library entry. The test did not modify an actual game installation.

CascLib extraction was checked against StarCraft II 5.0.16.97563 with enUS
installed. Windows and Linux produced matching counts:

| Campaign | Extracted files |
| --- | ---: |
| Wings of Liberty | 1084 |
| Heart of the Swarm | 1170 |
| Legacy of the Void, including prologue | 1124 |
| Nova Covert Ops | 389 |

These checks establish file extraction and placement, not successful gameplay.

On Windows, the tester confirmed Hyperion Crew loads in-game and Revert to base
restores the ordinary campaign. These checks used the dev3 build with direct
SC2 launching. Automatic sign-in through Battle.net was confirmed with dev4.

## In-game checks still needed

1. Repeat Play and campaign loading with other custom campaign packages.
2. Load a Hyperion Crew save from inside SC2 and check the crew mechanics.
3. Close and reopen the manager while SC2 is running. Confirm Load, Revert to
   base, and Remove stay unavailable. Exit SC2 while leaving Battle.net open
   and confirm the controls become available again.
4. Check the startup message with Battle.net signed out or waiting for an update.
5. Check HotS, LotV, and Nova campaign menus and saves in-game.
6. Repeat launching through Wine or Proton on the intended Linux setup.

Saves and banks remain shared with the game. Separate per-mod save profiles
and automatic compatibility checks are not implemented.

## Save backup checks

Automated checks cover saves and banks together, isolation between accounts,
backups before Load and Revert, stopping a switch when backup fails, restoring
current progress from the safety backup, retaining newer files, cancellation,
external edits, damaged payloads, unsafe paths, and recovery after abrupt
process termination during copying and after replacing a file.

For a manual check, open Saved progress and confirm the intended account.
Create a manual backup, switch campaigns, and confirm a new dated backup appears.
Test restoring with a copied account folder first. Automatic per-campaign save
and bank switching remains future work.

The backup/restore flow was also checked against a temporary copy of the test
account: 105 save files and 139 bank files. All 244 files matched their hashes
after restoring, and the source account files remained unchanged.

## Bank metadata checks

Automated checks cover the author editor, package imports with and without
bank declarations, excluding the companion file from game installation,
unsafe and duplicate paths, overlapping claims, stable ownership across
campaign versions, automatic account and bank backups before switching,
manual scope selection, and bank-only restores preserving unrelated banks
and SC2 saves. No ownership list has been inferred for Hyperion Crew.
