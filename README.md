# Force Stop for Cinnamon

Adds a **Force Stop** item to the right-click menu of the Grouped window list, for applications that no longer respond to Close.

![Force Stop below Close in the window list menu](screenshots/menu.png)

## What it does

- **Force Stop sits right below Close.** When an application has several windows, it sits below *Close all* instead.
- **It asks first.** The confirmation names the application and the process that is about to be stopped. `Escape` cancels it.
- **Optionally, it stops the processes the application started too.** This is off by default.
- **It knows when it cannot help.** The item is greyed out for windows without a process ID, and for processes of another user such as an application running as root.

Stopping means `SIGKILL`. The application gets no chance to save anything.

![The confirmation dialog](screenshots/confirmation-dialog.png)

## Settings

System Settings → Extensions → **Force Stop** → *Configure*.

![Force Stop settings](screenshots/settings.png)

| Setting | Default | What it does |
| --- | --- | --- |
| Ask before stopping an application | On | Shows the confirmation dialog. Turn it off to stop an application the moment you click Force Stop. |
| Also stop the processes it started | Off | Stops the helper processes of the application as well. This includes anything launched from its built-in terminal, such as a local web server or a database. |

Changes take effect at once. *More options → Reset to defaults* restores both.

## Requirements

- Cinnamon 6.6 with its standard *Grouped window list* applet. Tested on Cinnamon 6.6.9 (Linux Mint 22.3) in an X11 session. Other Cinnamon versions and Wayland are untested.
- The `kill` command, which every distribution ships, for the child processes setting.

## Install

From this repository:

```bash
./install.sh
```

Then enable *Force Stop* in System Settings → Extensions. To update, run `install.sh` again and disable and enable the extension.

## How it works

The extension notices every popup menu that Cinnamon creates and, whenever the window list fills its right-click menu, appends the Force Stop item.

Stopping is done by Muffin, the window manager: `Meta.Window.kill()` sends `SIGKILL` to the process that owns the window. For the child processes, the process tree is read from `/proc` before the application is stopped, and everything found is stopped together with it.

The window list is changed in memory only. No file of Cinnamon is touched. The extension does depend on the internals of the Grouped window list: when a Cinnamon update changes them, the item is missing until the extension is updated, and nothing else is affected.

## Tests

```bash
python3 tests/nested_test.py
```

This starts a private Cinnamon on Xephyr, with its own session bus, settings and extension directory, and drives it with real clicks. Its 31 checks cover the menu, the confirmation dialog, both settings, the process tree, and disabling and enabling. It needs `Xephyr`, `xdotool`, `zenity`, `xmessage` and ImageMagick.

A window titled "Force Stop test" appears for about three minutes. Leave it alone: Xephyr passes clicks on it into the nested desktop. Screenshots and logs of the last run end up in `tests/artifacts/`.

## Translations

User-visible strings are translatable. `force-stop@enisn/po/force-stop@enisn.pot` is the template.

## License

MIT
