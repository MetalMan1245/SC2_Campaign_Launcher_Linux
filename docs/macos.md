# macOS compatibility assessment

Reviewed October 25, 2026. macOS is not supported by the current launcher, and no
Mac build or live Mac gameplay test has been performed.

## Parts that can be reused

The Python campaign catalog, ZIP importer, bank scanner, bank lists, backup
format, hashing, and switching journals are mostly independent of the host OS.
The POSIX file lock uses fcntl, which also exists on macOS. PyQt6 supports both
Intel and Apple Silicon Macs, so the interface does not need a different toolkit.
The bank scanner uses Python's MPQ reader and bounded decompression; it does not
need a Windows DLL.

## Work required before a Mac test build

- Add a MacBackend. get_backend currently sends every non-Windows platform to
  LinuxBackend, which expects Wine or Proton.
- Recognize a real Mac SC2 installation. library.is_sc2_root and validate_root
  currently require Support64/SC2Switcher_x64.exe.
- Implement native SC2 and Battle.net launch handling. Verify app bundle paths,
  arguments, login behavior, and direct map launching on a Mac rather than
  assuming Windows command-line behavior transfers.
- Discover the Mac account Saves and Banks locations and use Application
  Support and Caches for launcher state. Current discovery supports Windows
  Documents and Wine prefix directories only.
- Replace Linux /proc process discovery and process identity tracking on Mac.
  Without this, running-game checks can incorrectly report that SC2 is stopped.
- Test installation permissions, case-sensitive volumes, app bundle locations,
  reversion, crash recovery, and multiple accounts using a real installation.
- Build an app bundle on macOS, with signing and notarization for distribution.
  The current build tool deliberately rejects non-Windows hosts. PyInstaller
  builds applications for its host platform rather than cross-compiling them.
- If base-game extraction and artwork are included, supply and test a compatible
  CascLib dylib. The bundled CascLib.dll is Windows-only; the loader already has
  a non-Windows shared-library lookup but has not been validated on Mac.

A CCM-only Mac edition is the smaller first target. Start with import, scan,
local bank lists, backup, load, and revert. Add Battle.net launching after those
operations are verified against the Mac game layout.

The independent Swagdude7 Mac launcher describes an Apple Silicon build and the
same user-created Maps and Mods folders. Its visible repository currently has
documentation rather than launcher source. It supports the feasibility of a Mac
edition, but does not provide an implementation we have reviewed or tested.

## Game compatibility is a separate requirement

A native Apple Silicon launcher would not by itself make SC2 or Battle.net
native. Check the installed game's architecture and behavior on the exact Mac
and OS version used for testing. Apple's current guidance says general Rosetta
support lasts through macOS 27, with limited support for certain older games
starting in macOS 28. Do not assume SC2 or Battle.net qualifies for that exception.
Blizzard's system-requirements page redirected to sign-in during this review,
so its current hardware and OS requirements were not independently confirmed.

References:

- [PyQt platform support](https://en.riverbankcomputing.com/software/pyqt)
- [PyInstaller supported platforms and host builds](https://pyinstaller.org/en/stable/)
- [Apple guidance on Intel apps and Rosetta](https://support.apple.com/en-us/102527)
- [Blizzard SC2 system requirements](https://us.support.blizzard.com/en/article/27575)
- [Independent Synergy Mac launcher](https://github.com/Swagdude7/SC2-Synergy-Launcher-Mac)
