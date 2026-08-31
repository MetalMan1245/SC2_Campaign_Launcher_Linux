# SC2_Campaign_Launcher_Linux
 Synergy's SC2 Campaign Launcher, completely rewritten for Linux
 
Note that this project does currently work for me, but is absolutely NOT ready for public release yet, as should be clear from the Features listed below.  I will also make an installer once I am happy with the feature set.  If you're vaguely familiar with how python works feel free to install the Dependencies and try it yourself but I am actively polishing this project to hopefully be feature compatible with the original Campaign Launcher project from R-P-S (321 on Discord)

# Upcoming Features

Add percentage to download indicator

Check shared mods, only redownload if updates are required, all maps with shared dependencies should be marked up to date if a different map was updated and the only difference is a shared mod file

Add a delete mods option in top left (present in original project)

Add prompt for SC2 and Wine discovery on first launch

Add auto discovery and easy dropdown list of Wine versions a-la Lutris and/or Heroic Launcher

Remove success dialog on downloading a campaign

Remove launch dialog when launching a campaign

Add support for moving campaigns when wine prefix is changed

Installer that detects distro and installs dependencies, creates .desktop shortcut and can uninstall.

Write out dependencies for this, python and umu-launcher for sure.

# License

StarCraft II is © Blizzard Entertainment.
I do not claim ownership of the any assets.

# Notes

This is a vibe coded project, I do use it myself but be warned, I am not an extrmely skilled developer, I just wanted to see an easier version of this exist for Linux.
