# SC2_Campaign_Launcher_Linux
 Synergy's SC2 Campaign Launcher, completely rewritten for Linux
 
Note that this project does currently work for me, but is absolutely NOT ready for public release yet, as should be clear from the Features listed below.  I will also make an installer once I am happy with the feature set.  If you're vaguely familiar with how python works feel free to install the Dependencies and try it yourself but I am actively polishing this project to hopefully be feature compatible with the original Campaign Launcher project from R-P-S (321 on Discord)

# Upcoming Features (roughly in order of importance)

Check shared mods, only redownload if updates are required, all maps with shared dependencies should be marked up to date if a different map was updated and the only difference is a shared mod file.

Add prompt for SC2 and Wine discovery on first launch.

Add auto discovery and easy dropdown list of Wine versions a-la Lutris and/or Heroic Launcher.

Installer that detects distro and installs dependencies, creates .desktop shortcut and can uninstall.

Add a delete mods option in top left as well as solarite icon and links.

Add support for moving campaigns when wine prefix is changed (or at least remove previous campaigns, launcher will still think campaigns are installed if Wine prefix is changed).

Write out dependencies for this, python, PyQt6, and umu-launcher for sure.

Add percentage to download indicator.

Remove success dialog on downloading a campaign.

Remove launch dialog when launching a campaign.

# License

StarCraft II is © Blizzard Entertainment.
I do not claim ownership of the any assets.

# Notes

This is a vibe coded project, I do use it myself but be warned, I am not an extrmely skilled developer, I just wanted to see an easier version of this exist for Linux.

The oriinal SC2 Campaign Launcher that is Windows exclusive can be found here (no source code): https://github.com/R-P-S/SC2CampaignLauncher

The maps for the launcher are kept here: https://github.com/R-P-S/SC2Campaigns

I took inspiration from ZachZimm who originally ported the project to Linux, and he's been very helpful and supportive, this version is more complicated but worth a look since it's the original launcher by R-P-S: https://github.com/ZachZimm/Synergy-Mod-Launcher-Linux

I haven't used it myself but there is supposed to be a macOS version of the launcher, check it out if you're on macOS: https://github.com/Swagdude7/SC2-Synergy-Launcher-Mac

# Ignore this I'm documenting map versions shown in the app for testing

08/31/2026

Battlegrounds: 2.97
Gods: 2.6
Legends: 3.11
Mysteries: 2.17
Reborn: 3.78
Reswapped: 2.39
Deliverance: 1.8
HotsPE: 1.9
UEDFL: 1.14
