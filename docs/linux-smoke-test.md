# Linux smoke test

Use the supplied complete source checkout and README setup instructions with an
existing working SC2 Wine/Proton installation. The developer's WSL checks cover
automated behavior; this checklist verifies play on the target Linux machine.

1. Close SC2. Record the launcher `--version` and `--build-info`, distribution,
   desktop, runner/version, SC2 build, and campaign/package version. In Settings,
   select the intended SC2 directory, runner, and existing Wine prefix. With UMU,
   an already configured custom runner avoids a managed download during this test.
2. Keep a separate copy of your account's Saves and Banks. In **Saved progress**,
   confirm the correct account and make a backup. Keep your original campaign ZIP.
3. **Import** one known-compatible CCM campaign ZIP or folder. Confirm its title
   and campaign slot. Click **Load** with SC2 closed; expect its card to say Loaded.
4. Click **Play**. Start a new mission inside SC2, check the expected custom
   content, save, and continue/load that save inside the game. Note any sign-in,
   runner, missing-file or black-screen failure and the exact action that caused it.
5. While SC2 is running, close and reopen the manager. Confirm Load, Revert and
   Remove remain blocked. Quit SC2 completely; controls should become available
   again even if an unrelated launcher remains open.
6. Click **Revert to base**, then open SC2 and start an ordinary mission in that
   campaign slot. Confirm the base content returns and the imported custom package
   remains available. Keep base/custom saves separate for this check.

For an ordinary catalog campaign, also check a queued download and cancellation;
retry should resume by keeping completed verified files. A resource-limit error
names its setting; check free space and the known package size before changing it.

If a step fails, record pass/fail per step, version/build info, distro/desktop,
runner/version, SC2 build, campaign version, and the exact error. The launcher logs
are normally in `~/.local/share/SC2CampaignLauncher/logs` (or under XDG_DATA_HOME).
Review a short relevant excerpt and redact usernames/paths or account details you
do not want to share. Do not include passwords, tokens, cookies, full environment
dumps, account IDs, or save/bank contents. No one needs account credentials for
this test. Do not delete pending recovery records to make a failing step proceed.
