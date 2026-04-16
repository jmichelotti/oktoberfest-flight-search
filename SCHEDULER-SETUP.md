Windows Task Scheduler Setup — Oktoberfest Flight Search

The task runs twice a day (08:00 and 20:00) and kicks off `run-tracker.bat`, which calls `claude -p` to execute the workflow described in `CLAUDE.md`.

## Creating / recreating the task (command line)

Run from any shell that can invoke `schtasks` (Git Bash, PowerShell, cmd). `//` escapes are for Git Bash; in cmd/PowerShell use single `/`.

```
schtasks //create //tn "Oktoberfest Flight Search" //tr "C:\dev\oktoberfest-flight-search\run-tracker.bat" //sc DAILY //st 08:00 //sd 04/15/2026 //f
```

Then open Task Scheduler GUI (`taskschd.msc`) → right-click the task → Properties → **Triggers** tab → edit the trigger → check **Repeat task every 12 hours** for a duration of **1 day**. That gives runs at 08:00 and 20:00.

`/f` overwrites an existing task with the same name, so the create command is idempotent.

## Verifying

```
schtasks //query //tn "Oktoberfest Flight Search" //v //fo LIST
```

Expect `Next Run Time` to be today or tomorrow at 08:00 and `Status` to be `Ready`.

## Running on demand

```
schtasks //run //tn "Oktoberfest Flight Search"
```

Then `tail -f tracker-log.txt` in the project dir to watch output.

## Deleting

```
schtasks //delete //tn "Oktoberfest Flight Search" //f
```

## Editing in the GUI

`taskschd.msc` → Task Scheduler Library → `Oktoberfest Flight Search`. Use the GUI for less-common tweaks:

- **Conditions** tab → uncheck "Start the task only if the computer is on AC power" (if running on a laptop).
- **Settings** tab → check "Run task as soon as possible after a scheduled start is missed" (recommended — catches missed runs when the machine was off).

## Troubleshooting

- `Last Result` of `0x0` = success. Anything else: inspect `tracker-log.txt` for the claude session output.
- "Interactive only" logon mode means the task only fires when the user is logged in. Leave on unless you need unattended-login runs (which would require storing credentials in Task Scheduler — avoid).
- If `claude` isn't found when the task runs, add `set PATH=%PATH%;C:\Users\thunderhead\AppData\Local\...` to `run-tracker.bat` before the `claude -p` line.
