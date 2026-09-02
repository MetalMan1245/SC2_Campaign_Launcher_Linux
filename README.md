# SC2_Campaign_Launcher_Linux
 Synergy's SC2 Campaign Launcher, completely rewritten for Linux
 
Note that this project does currently work for me, but is absolutely NOT ready for public release yet, as should be clear from the Features listed below.  I will also make an installer once I am happy with the feature set.  If you're vaguely familiar with how python works feel free to install the Dependencies and try it yourself but I am actively polishing this project to hopefully be feature compatible with the original Campaign Launcher project from R-P-S (321 on Discord)

# Upcoming Features (roughly in order of importance)

Add info and settings icons if they fit the Linux UI.

Add support for moving campaigns when wine prefix is changed (or at least remove previous campaigns, launcher will still think campaigns are installed if Wine prefix is changed).

Add a "last updated" field in campaign details.

Updater to install/uninstall script.

# License

StarCraft II is © Blizzard Entertainment.
I do not claim ownership of the any assets.

# Notes

This is a vibe coded project, I do use it myself but be warned, I am not an extrmely skilled developer, I just wanted to see an easier version of this exist for Linux.

The oriinal SC2 Campaign Launcher that is Windows exclusive can be found here (no source code):

https://github.com/R-P-S/SC2CampaignLauncher

The maps for the launcher are kept here:

https://github.com/R-P-S/SC2Campaigns

I took inspiration from ZachZimm who originally ported the project to Linux, and he's been very helpful and supportive, this version is more complicated but worth a look since it's the original launcher by R-P-S:

https://github.com/ZachZimm/Synergy-Mod-Launcher-Linux

I haven't used it myself but there is supposed to be a macOS version of the launcher, check it out if you're on macOS:

https://github.com/Swagdude7/SC2-Synergy-Launcher-Mac

# Ignore this it's just development notes

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

Add a prompt for SC2 and Wine discovery on first launch.  The flow should be a prompt to either auto detect SC2's location or manually define it, auto detection should probably search for Wine prefixes on the system, then see if any have drive_c/Program Files (x86)/StarCraft II inside them (if it's installed to a custom location then they can manually find it, that's fine by me) and then show the user where it found SC2 so that the user can verify if that is correct.  And if multiple instances of SC2 are found prompt the user to pick one.  I am open to optimizations for this but I think just searching ~/.wine, ~/Games, ~/.local/share/Steam/steamapps/common/, and ~/.steam first, then prompting the user to elevate the search to either just the home directory or the root directory (specified that it will likely take some time and is not recommended, especially for root, preferably with an estimate of how long it will take to parse each file system) after that should be fine, if the user hasn't installed it in any of those common locations then they probably already know where it is.

drive_c/Program Files (x86)/StarCraft II under known prefix dirs (~/Games/umu/*, ~/.local/share/umu/, ~/.steam/steam/steamapps/compatdata/...), plus mounted Windows partitions

Wine discovery should be handled after that, preferably read how heroic does its wine discovery since it has a very clean UI for it, and the launcher should default to either Valve's Proton (Experimental), CachyOS Proton, or any other Proton found in that order, and warn the user if all it finds is normal Wine since in my experience that does not generally work very well for StarCraft 2.
