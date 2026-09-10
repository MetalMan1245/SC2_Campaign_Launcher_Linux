# SC2 Campaign Launcher

Download and play the custom campaigns published in
[R-P-S/SC2Campaigns](https://github.com/R-P-S/SC2Campaigns).
The launcher runs on Linux with Wine or Proton, and on Windows.
StarCraft II must already be installed.

## Linux installation

Requirements:

- Python 3.10 or newer
- PyQt6 6.2.3 or newer
- [UMU](https://github.com/Open-Wine-Components/umu-launcher#installing) for Proton,
  or a working Wine installation
- An existing Wine prefix with StarCraft II installed

From a complete source checkout, run:

```sh
bash install-uninstall-SC2CLL.sh
```

The installer detects existing local and custom installations and offers the
corresponding update and removal actions. On a new installation it asks where
to install. It offers to install the distribution's PyQt6 package when needed.
It does not install StarCraft II or create a new Wine prefix.
The launcher appears in the application menu after installation.

To select a local installation directly, use `--install local`.

For a custom installation, choose a parent directory:

```sh
bash install-uninstall-SC2CLL.sh --install custom --directory "$HOME/Applications"
```

This creates `$HOME/Applications/SC2CampaignLauncher`. The application directory
must be empty on the first installation. Paths containing spaces are supported.
Local and custom installations have separate records and menu entries.

Release installers download a specific release tag. The desktop installer in
this checkout targets `v1.2.0` and becomes usable after that tag is published.
Until then, use the source installation above. For a specific reviewed commit:

```sh
SC2CL_REF=<full-commit-id> bash synergy_remote-installer.sh --install local
```

The remote installer requires curl and unzip. A release installer continues to
install its named version; download a newer installer when updating.

## Run from source

A virtual environment is useful when the distribution's PyQt6 package is older
than the supported version:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
sc2-campaign-launcher
```

To run the checkout directly in that environment:

```sh
python -m sc2_campaign_launcher_linux
```

The original script entry point also works:

```sh
python sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py
```

The complete checkout is required for its artwork. To install a menu entry using
the virtual environment, set `SC2CL_PYTHON` to the absolute path of its Python
executable when running `install-uninstall-SC2CLL.sh`. Keep that environment while
the application is installed.

## Windows

Download `SC2CampaignLauncher-Windows-x86_64.zip` from a release and extract the
whole archive. Keep `_internal` beside `SC2CampaignLauncher.exe`. The executable
depends on the Python and Qt files in that directory.

Choose the StarCraft II directory on first launch. Wine and Proton settings are
not used on Windows.

## Setup and campaign files

Select the directory containing `Support64/SC2Switcher_x64.exe`. The Scan button
checks common Steam, Wine, UMU, and Bottles locations. Browse can select an
installation elsewhere.

On Linux, select either a discovered Wine/Proton version or **UMU managed Proton**.
UMU finds and downloads its managed Proton version when first used. A standalone
Wine selection is launched directly through Wine.

Prefix detection uses the `drive_c` directory in the selected SC2 path, including
paths where the game directory is a symlink. If SC2 is on another
drive, turn off automatic detection and select its prefix. Maps outside `drive_c`
need a matching drive mapping under the prefix's `dosdevices` directory. These
can be configured with winecfg. The UMU executable can be selected explicitly
when it is not available on the desktop session's PATH.

In Settings, **Refresh** checks the catalog and compares installed maps and mods
with it. **Verify files** rehashes every listed file without using cached results.
After a failed launch, **Verify / repair** checks and downloads any missing or
changed files for that campaign. Downloads are queued, can be cancelled, and are verified
before replacing existing files. A cancelled installation keeps completed files
so it can be resumed.

Maps go under `Maps/<campaign folder>` and shared mods go under `Mods` in the
selected SC2 installation. Symlinks inside these managed paths are rejected.
Each SC2 installation has separate download and ownership records.

The trash icon removes campaign files whose contents match the current catalog
or a version recorded by the launcher. This also works for older installations
without download records. Modified and unknown files are kept, along with shared
mods still needed by another known campaign. The information icon shows campaign
details; hovering over it shows the campaign summary.

When the catalog is unavailable, the last valid catalog and recorded campaigns
remain available. This does not change StarCraft II's own offline requirements.
Closing the launcher leaves a started game process running.
Reopening the launcher recognizes active game monitors and prevents campaign
file changes until they finish. A separate monitor drains game output and keeps
each game log within 1 MiB, including while the launcher is closed.

## Updating and removing the launcher

Run the installer from a new source checkout or release to update the application.
It checks for local edits before replacing recorded application files. Move any
edited files to a backup location before retrying an update.

The launcher's desktop entry includes an **Uninstall** action with a confirmation
dialog. From a source checkout, `--uninstall` detects the installed scope. To
select a scope explicitly:

```sh
bash install-uninstall-SC2CLL.sh --uninstall local
bash install-uninstall-SC2CLL.sh --uninstall custom
bash install-uninstall-SC2CLL.sh --uninstall both
```

Uninstall removes only unchanged application files recorded by the installer.
It keeps campaigns, preferences, download records, and unrelated files. A previous
desktop entry replaced during installation is restored if it has not since been
edited.

The old copied `~/.local/bin/install-uninstall-SC2CLL.sh`, when present, is backed
up as a text file under the configuration directory and replaced with a wrapper
for the current installer. It no longer recursively removes a custom directory.
Older application directories are kept because they have no file ownership
record. Remove those manually after checking their contents.

## Troubleshooting

Launch failures include the runner's exit code and recent output, with a button
to copy the details. Logs are under the application data directory:

- Linux: `$XDG_DATA_HOME/SC2CampaignLauncher/logs`, normally
  `~/.local/share/SC2CampaignLauncher/logs`
- Windows: `%LOCALAPPDATA%\SC2CampaignLauncher\logs`

Linux catalog and cover caches use `$XDG_CACHE_HOME/SC2CampaignLauncher`, normally
`~/.cache/SC2CampaignLauncher`. Installer records use
`$XDG_CONFIG_HOME/SC2CampaignLauncher`, normally `~/.config/SC2CampaignLauncher`.

If automatic prefix detection is empty, select the existing prefix manually.
If a custom Proton build fails, try the managed UMU option and include the launch
log in a bug report. Include the distribution, desktop session, runner version,
and campaign name.

Qt chooses Wayland or X11 from the desktop session. If a particular system needs
the X11 fallback, launch with `QT_QPA_PLATFORM=xcb`; it is not forced by the
installed desktop entry.

## Development and release builds

```sh
python -m pip install -e ".[test]"
python -m pytest -q
python -m ruff check .
```

Tests use isolated directories, a local HTTP server, mocked downloads, and offscreen Qt. They cover
file verification, failed writes, cancellation, shared mods, install scopes,
worker lifetime, runner arguments, and process lifetime. They do not substitute
for launching StarCraft II on the intended Wine/Proton and desktop setup.

On Linux, also run:

```sh
bash -n install-uninstall-SC2CLL.sh synergy_remote-installer.sh
shellcheck install-uninstall-SC2CLL.sh synergy_remote-installer.sh
desktop-file-validate SC2-Campaign-Launcher-Linux_Install-Uninstall.desktop
```

Build the Windows release on Windows:

```powershell
python -m pip install -e ".[build]"
python tools/build_release.py
```

The build checks Qt and artwork, then writes the complete distribution to
`dist/SC2CampaignLauncher-Windows-x86_64.zip`. Publish that ZIP, not the executable
alone. CI also produces the ZIP as a build artifact without publishing a release.

When changing the release version, update `pyproject.toml`, the package version,
and `RELEASE_REF` in `synergy_remote-installer.sh`, then run
`python tools/write_installer_entry.py` to update the desktop installer.

## Planned work

- Additional campaign sources beyond the current catalog
- Moving installed campaigns between SC2 directories
- Sorting by author, update date, or map count, and displaying update dates
- Exploring SC2 installation through [Naturalize](https://github.com/MetalMan1245/naturalize)

## Credits and license

Campaigns and the original launcher are by Synergy and the respective campaign
authors. The [original Windows launcher](https://github.com/R-P-S/SC2CampaignLauncher)
is available separately. This Linux implementation was inspired by
[ZachZimm's port](https://github.com/ZachZimm/Synergy-Mod-Launcher-Linux).
An independent [macOS version](https://github.com/Swagdude7/SC2-Synergy-Launcher-Mac)
is also available.

The launcher is licensed under GPL-3.0; see LICENSE. StarCraft II belongs to
Blizzard Entertainment. Campaign assets belong to their respective owners.
