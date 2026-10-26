# CascLib

CascLib.dll is the 64-bit ANSI build of CascLib 3.0 used for reading local
StarCraft II CASC data. It was taken from the existing sc2-ui-editor toolchain.
CascLib-LICENSE.txt contains its MIT license.

The Python binding expects the CascLib 3.x find-data structure. Linux builds
use a separately installed libcasc shared library. No game assets are bundled.

Linux extraction was checked with the same CascLib source compiled as a shared
library using GCC on Ubuntu 24.04. All four base campaigns produced the same
file counts as the Windows DLL. A Linux binary is not bundled; compile or
install CascLib 3.x for the target distribution. SC2CL_CASCLIB can point to the
resulting libcasc.so without changing the system loader configuration.
