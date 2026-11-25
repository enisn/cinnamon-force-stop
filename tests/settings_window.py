#!/usr/bin/env python3
"""Test-only: opens the settings page of an xlet the way Cinnamon does, saves a screenshot of it and
can flip its switches through the actual widgets. Started by tests/nested_test.py inside the
private session; never run it against the desktop session.

Usage: settings_window.py UUID SCREENSHOT|- [SETTING=on|off ...]
"""
import importlib.util
import json
import os
import sys
import traceback
import types

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('XApp', '1.0')
from gi.repository import Gdk, GLib, Gtk

CINNAMON_SETTINGS = '/usr/share/cinnamon/cinnamon-settings'


def widgets(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from widgets(child)


def main():
    uuid, screenshot, *changes = sys.argv[1:]
    sys.path.insert(0, CINNAMON_SETTINGS)
    spec = importlib.util.spec_from_file_location('xlet_settings', os.path.join(CINNAMON_SETTINGS, 'xlet-settings.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def later(delay, step):
        GLib.timeout_add(int(delay * 1000), step)

    def step_opened():
        # The window opens at 800x600, which would leave two switches lost in empty space.
        natural = page.window.get_child().get_preferred_size()[1]
        page.window.resize(max(natural.width, 560), natural.height)
        later(1.5, step_capture)

    def step_capture():
        if screenshot != '-':
            window = page.window.get_window()
            # The title bar with the name and icon is drawn by the window manager, so the whole
            # frame is taken from the screen rather than from the window alone.
            frame = window.get_frame_extents()
            if frame.height > window.get_height() or frame.width > window.get_width():
                pixbuf = Gdk.pixbuf_get_from_window(window.get_screen().get_root_window(),
                                                    frame.x, frame.y, frame.width, frame.height)
            else:
                pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, window.get_width(), window.get_height())
            pixbuf.savev(screenshot, 'png', [], [])
        step_flip()

    def step_flip():
        # Widgets come in the order in which the schema lists them.
        with open(os.path.join(page.xlet_dir, 'settings-schema.json')) as handle:
            schema = json.load(handle)
        switch_keys = [key for key, setting in schema.items() if setting.get('type') == 'switch']
        combo_keys = [key for key, setting in schema.items() if setting.get('type') == 'combobox']
        switches = [widget for widget in widgets(page.window) if isinstance(widget, Gtk.Switch)]
        combos = [widget for widget in widgets(page.window) if isinstance(widget, Gtk.ComboBox)]
        for change in changes:
            key, value = change.split('=', 1)
            if key in switch_keys:
                switches[switch_keys.index(key)].set_active(value == 'on')
            elif key in combo_keys:
                combo = combos[combo_keys.index(key)]
                for row in combo.get_model():
                    if row[0] == value:
                        combo.set_active_iter(row.iter)
                        break
        later(0.8, lambda: page.window.destroy())

    page = module.MainWindow(types.SimpleNamespace(type='extension', uuid=uuid, id=None, tab=None))
    later(1.0, step_opened)
    Gtk.main()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
