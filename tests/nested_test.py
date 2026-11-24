#!/usr/bin/env python3
"""End-to-end test for force-stop@enisn inside a private Cinnamon on Xephyr.

The nested Cinnamon gets its own X display, session bus, settings file and xlet directory, so
nothing in the desktop session is read from or written to. The session bus has no service
directories, so nothing in the nested session can start system daemons. A window titled
"Force Stop test" shows the nested desktop while the test runs, and must be left alone: Xephyr
forwards whatever happens over it into the nested desktop. Screenshots and logs end up in
tests/artifacts/.

Usage: python3 tests/nested_test.py
"""
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Temporary files stay out of /tmp, which disk cleaners and other tools like to touch.
RUNTIME = os.environ.get('XDG_RUNTIME_DIR') or os.path.dirname(ROOT)
UUID = 'force-stop@enisn'
DRIVER = 'zz-force-stop-driver@enisn'
WINDOW_LIST = 'grouped-window-list@cinnamon.org'
DISPLAY = os.environ.get('NESTED_DISPLAY', ':77')
SCREEN = '1366x768x24'
ARTIFACTS = os.path.join(ROOT, 'tests', 'artifacts')
PR_SET_CHILD_SUBREAPER = 36

PRELUDE = r'''
const AppletManager = imports.ui.appletManager;
const Extension = imports.ui.extension;
const ExtensionSystem = imports.ui.extensionSystem;
const Main = imports.ui.main;
const PopupMenu = imports.ui.popupMenu;
const UUID = %(uuid)s;
const WINDOW_LIST = %(window_list)s;
const extension = () => ExtensionSystem.get_object_for_uuid(UUID);
function groups() {
    const found = [];
    for (const definition of AppletManager.definitions || []) {
        const applet = definition && definition.applet;
        if (!applet || applet._uuid !== WINDOW_LIST) continue;
        for (const workspace of applet.workspaces || [])
            for (const group of (workspace && workspace.appGroups) || [])
                if (group) found.push(group);
    }
    return found;
}
// A group is looked up by the PID of one of its windows, or by the class name of its focused window.
function groupFor(key) {
    return groups().find(group => typeof key === 'number' ?
        group.groupState.metaWindows.some(w => w.get_pid() === key) :
        !!(group.groupState.lastFocused &&
           String(group.groupState.lastFocused.get_wm_class()).toLowerCase() === key)) || null;
}
function labels(menu) {
    return menu._getMenuItems().map(item => item instanceof PopupMenu.PopupSeparatorMenuItem ? '---' :
        (item.label ? item.label.text : '?'));
}
function center(actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return [Math.round(x + w / 2), Math.round(y + h / 2)];
}
function forceStopItem(menu) {
    return menu._getMenuItems().find(item => item.label && item.label.text === 'Force Stop') || null;
}
function state(key) {
    const group = groupFor(key);
    const menu = group && group.rightClickMenu;
    const dialog = extension() && extension()._dialog;
    const content = dialog && dialog.contentLayout.get_first_child();
    return {
        group: !!group,
        targetPid: group && group.groupState.lastFocused ? group.groupState.lastFocused.get_pid() : null,
        menu: !!menu,
        menuOpen: !!(menu && menu.isOpen),
        labels: menu && menu.isOpen ? labels(menu) : null,
        dialog: dialog ? {title: content._title.text, description: content._description.text} : null,
        modal: Main.modalActorFocusStack.length,
    };
}
''' % {'uuid': json.dumps(UUID), 'window_list': json.dumps(WINDOW_LIST)}

CONFIRM_BUTTON = (
    'const dialog = extension()._dialog;'
    'return JSON.stringify(center(dialog.buttonLayout.get_children().find(b => b.label === "Force Stop")));')


class Fail(Exception):
    pass


RESULTS = []


def check(ok, what):
    RESULTS.append(bool(ok))
    print(('ok   ' if ok else 'FAIL ') + what, flush=True)


def wait_until(probe, what, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.25)
    raise Fail('timed out waiting for ' + what)


def exit_status(app):
    try:
        return app.wait(10)
    except subprocess.TimeoutExpired:
        raise Fail('process %d is still running' % app.pid)


def alive(pid):
    """False once a process has exited, zombies included."""
    try:
        with open('/proc/%d/stat' % pid) as handle:
            return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def read_pid(path):
    with open(path) as handle:
        return int(handle.read())


class Nested:
    """The private Xephyr display, the session bus and the Cinnamon running on them."""

    def __init__(self):
        # Processes that the extension stops leave children behind. As a subreaper this process
        # adopts such orphans, so that stop() can reap them instead of leaving zombies around.
        ctypes.CDLL(None).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        self.work = tempfile.mkdtemp(prefix='force-stop-nested-', dir=RUNTIME)
        self.eval_dir = os.path.join(self.work, 'eval')
        self.log_path = os.path.join(ARTIFACTS, 'cinnamon.log')
        self.requests = 0
        self.display_env = dict(os.environ, DISPLAY=DISPLAY)
        self.bus = self.xephyr = self.cinnamon = None
        self.apps = []

    def start(self):
        # A session bus without service directories: nothing in the nested session can start daemons.
        config = os.path.join(self.work, 'session.conf')
        with open(config, 'w') as handle:
            handle.write('<busconfig>\n<type>session</type>\n<listen>unix:tmpdir=%s</listen>\n'
                         '<auth>EXTERNAL</auth>\n<policy context="default">\n<allow own="*"/>\n'
                         '<allow send_destination="*" eavesdrop="true"/>\n<allow eavesdrop="true"/>\n'
                         '</policy>\n</busconfig>\n' % self.work)
        self.bus = subprocess.Popen(['dbus-daemon', '--nofork', '--print-address', '--config-file', config],
                                    stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.bus_address = self.bus.stdout.readline().strip()
        if not self.bus_address.startswith('unix:'):
            raise Fail('the private session bus did not start: %s' % self.bus_address)

        # Earlier results stay where they are; a run only replaces the files it produces itself.
        os.makedirs(ARTIFACTS, exist_ok=True)
        self.xephyr_log = open(os.path.join(ARTIFACTS, 'xephyr.log'), 'w')
        self.xephyr = subprocess.Popen(
            ['Xephyr', DISPLAY, '-ac', '-no-host-grab', '-screen', SCREEN,
             '-name', 'force-stop-test', '-title', 'Force Stop test'],
            stdout=subprocess.DEVNULL, stderr=self.xephyr_log, start_new_session=True)
        wait_until(self.display_responds, 'the nested display')
        self.run_display_tool(['xdotool', 'getmouselocation'])

        applications = os.path.join(self.work, 'data', 'applications')
        os.makedirs(applications)
        # One desktop entry for all test windows, so that they share one window list button
        # like the windows of a real application.
        with open(os.path.join(applications, 'zenity.desktop'), 'w') as handle:
            handle.write('[Desktop Entry]\nType=Application\nName=Zenity\nExec=zenity\n'
                         'StartupWMClass=zenity\n')

        data = os.path.join(self.work, 'data', 'cinnamon', 'extensions')
        for uuid, source in ((UUID, os.path.join(ROOT, UUID)), (DRIVER, os.path.join(ROOT, 'tests', 'driver'))):
            shutil.copytree(source, os.path.join(data, uuid))
        # The settings window looks for xlets under the home directory rather than XDG_DATA_HOME.
        home_extensions = os.path.join(self.work, 'home', '.local', 'share', 'cinnamon', 'extensions')
        os.makedirs(home_extensions)
        os.symlink(os.path.join(data, UUID), os.path.join(home_extensions, UUID))

        self.env = os.environ.copy()
        self.env.update({
            'DBUS_SESSION_BUS_ADDRESS': self.bus_address,
            'DISPLAY': DISPLAY,
            'XDG_CONFIG_HOME': os.path.join(self.work, 'config'),
            'XDG_DATA_HOME': os.path.join(self.work, 'data'),
            'FS_TEST_EVAL_DIR': self.eval_dir,
            'GSETTINGS_BACKEND': 'keyfile',
            'LIBGL_ALWAYS_SOFTWARE': '1',
        })
        self.env.pop('SESSION_MANAGER', None)
        self.settings_file = os.path.join(self.env['XDG_CONFIG_HOME'], 'cinnamon', 'spices', UUID, UUID + '.json')

        # The file-based settings backend keeps the nested Cinnamon away from dconf and D-Bus
        # activation. It stores each key as GVariant text, so the values are written verbatim.
        keyfile = os.path.join(self.env['XDG_CONFIG_HOME'], 'glib-2.0', 'settings', 'keyfile')
        os.makedirs(os.path.dirname(keyfile), exist_ok=True)
        with open(keyfile, 'w') as handle:
            handle.write('[org/cinnamon]\n')
            handle.write("enabled-applets=['panel1:center:0:%s:1']\n" % WINDOW_LIST)
            handle.write("enabled-extensions=['%s', '%s']\n" % (UUID, DRIVER))

        with open(self.log_path, 'w') as log:
            self.cinnamon = subprocess.Popen(['cinnamon', '--replace', '--sm-disable'], env=self.env,
                                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        wait_until(self.driver_ready, 'the test driver in the nested Cinnamon', 120)

    def display_responds(self):
        if self.xephyr.poll() is not None:
            raise Fail('Xephyr exited with status %s' % self.xephyr.returncode)
        return subprocess.run(['xdotool', 'getdisplaygeometry'], env=self.display_env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def run_display_tool(self, command):
        env = dict(os.environ, DISPLAY=DISPLAY)
        subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=20)

    def driver_ready(self):
        if self.cinnamon.poll() is not None:
            raise Fail('the nested Cinnamon exited with status %s, see %s' % (self.cinnamon.returncode, self.log_path))
        return os.path.exists(os.path.join(self.eval_dir, 'ready'))

    def stop(self):
        processes = self.apps + [self.cinnamon, self.xephyr, self.bus]
        for process in processes:
            if process is not None and process.poll() is None:
                # Each child leads its own session, so its group holds nothing but its own offspring.
                os.killpg(process.pid, 15)
        for process in processes:
            if process is None or process.poll() is not None:
                continue
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait(5)
        self.reap()
        self.bus.stdout.close()
        self.xephyr_log.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def reap(self):
        """Collects the orphans this process adopted, including the ones the extension stopped."""
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return

    def ev(self, code):
        """Runs JavaScript inside the nested Cinnamon and returns what it returns as a string."""
        self.requests += 1
        request = os.path.join(self.eval_dir, '%03d.js' % self.requests)
        outcome = request[:-3] + '.out'
        with open(request, 'w') as handle:
            handle.write(PRELUDE + code)
        deadline = time.time() + 20
        while not os.path.exists(outcome):
            if time.time() > deadline:
                raise Fail('no answer from the test driver for: ' + code.strip()[:80])
            time.sleep(0.05)
        with open(outcome) as handle:
            text = handle.read()
        if text.startswith('__ERROR__'):
            raise Fail(text)
        return text

    def state(self, key):
        return json.loads(self.ev('return JSON.stringify(state(%s));' % json.dumps(key)))

    def setting(self, key):
        return json.loads(self.ev(
            'return JSON.stringify({confirm: extension().confirm, "kill-children": extension().killChildren});'))[key]

    def wait_setting(self, key, value):
        wait_until(lambda: self.setting(key) is value, 'the %s setting to reach the extension' % key)

    def read_settings(self):
        with open(self.settings_file) as handle:
            settings = json.load(handle)
        return {key: setting['value'] for key, setting in settings.items() if 'value' in setting}

    def item(self, key):
        return json.loads(self.ev(
            'const item = forceStopItem(groupFor(%s).rightClickMenu);'
            'return JSON.stringify({sensitive: item.sensitive, center: center(item.actor)});' % json.dumps(key)))

    def launch(self, name, *command):
        if not command:
            command = ('zenity', '--info', '--title=ForceStopTest%s' % name,
                       '--text=Force Stop test window', '--width=360')
        app = subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        self.apps.append(app)
        return app

    def launch_with_children(self):
        """A test window whose process has a child, which has a child of its own."""
        directory = os.path.join(self.work, 'tree-%d' % len(self.apps))
        os.makedirs(directory)
        child, middle, grandchild = (os.path.join(directory, name) for name in ('child', 'middle', 'grandchild'))
        app = subprocess.Popen(['bash', '-c', (
            'sleep 600 & echo $! > {child}; '
            '( sleep 600 & echo $! > {grandchild}; exec sleep 600 ) & echo $! > {middle}; '
            'exec zenity --info --title=ForceStopTestTree --text="Force Stop test window" --width=360'
        ).format(child=child, grandchild=grandchild, middle=middle)], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.apps.append(app)
        wait_until(lambda: all(os.path.exists(path) for path in (child, middle, grandchild)),
                   'the process tree to start')
        time.sleep(0.3)
        return app, [read_pid(path) for path in (child, middle, grandchild)]

    def xdo(self, *args):
        self.run_display_tool(['xdotool'] + [str(arg) for arg in args])

    def shot(self, name):
        self.run_display_tool(['import', '-window', 'root', os.path.join(ARTIFACTS, name)])

    def open_settings(self, screenshot, *changes):
        """Opens Cinnamon's settings page of the extension, saves a screenshot, then flips switches."""
        env = dict(self.env, HOME=os.path.join(self.work, 'home'))
        result = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'tests', 'settings_window.py'),
                                 UUID, screenshot if screenshot == '-' else os.path.join(ARTIFACTS, screenshot)]
                                + list(changes), env=env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise Fail('the settings window failed:\n' + result.stderr.strip()[-600:])

    def open_menu(self, key):
        try:
            x, y = json.loads(self.ev('return JSON.stringify(center(groupFor(%s).actor));' % json.dumps(key)))
        except Fail as failure:
            # Xephyr forwards whatever happens over its window on the real desktop into the nested one.
            if 'groupFor(...) is null' in str(failure):
                app = next((app for app in self.apps if app.pid == key), None)
                raise Fail('the window %s left the window list before the test could right-click it (%s). '
                           'Keep the mouse and keyboard away from the "Force Stop test" window.'
                           % (key, 'exited with status %s' % app.poll() if app and app.poll() is not None
                              else 'still running'))
            raise
        self.xdo('mousemove', x, y, 'click', 3)
        time.sleep(0.6)
        return self.state(key)['labels']

    def close_menu(self, key):
        self.xdo('key', 'Escape')
        wait_until(lambda: not self.state(key)['menuOpen'] and self.state(key)['modal'] == 0,
                   'the menu to close')

    def force_stop(self, key):
        """Right-clicks the window list button, unless its menu is open, and clicks Force Stop."""
        if not self.state(key)['menuOpen']:
            self.open_menu(key)
        x, y = self.item(key)['center']
        self.xdo('mousemove', x, y, 'click', 1)

    def confirm_dialog(self):
        """Clicks Force Stop in the open confirmation dialog."""
        wait_until(lambda: self.ev('return String(!!extension()._dialog);') == 'true', 'the confirmation dialog')
        x, y = json.loads(self.ev(CONFIRM_BUTTON))
        self.xdo('mousemove', x, y, 'click', 1)


def run_tests(n):
    loaded = json.loads(n.ev(
        'return JSON.stringify({object: !!extension(), running: ExtensionSystem.runningExtensions.includes(UUID),'
        'modal: Main.modalActorFocusStack.length});'))
    check(loaded == {'object': True, 'running': True, 'modal': 0},
          'the shell enabled the extension and holds no modal')
    check(n.read_settings() == {'confirm': True, 'kill-children': False} and
          n.setting('confirm') is True and n.setting('kill-children') is False,
          'the settings are installed with their defaults: confirmation on, child processes off')

    # First right-click ever: the window list creates its menu lazily, so the hook must catch it.
    first = n.launch('One')
    wait_until(lambda: n.state(first.pid)['group'], 'the first window to appear in the window list')
    check(not n.state(first.pid)['menu'], 'the window list creates its right-click menu lazily')
    labels = n.open_menu(first.pid)
    check(labels is not None and 'Force Stop' in labels and
          labels.index('Force Stop') == labels.index('Close') + 1,
          'a freshly created right-click menu lists Force Stop right after Close')
    n.shot('01-menu.png')
    check(n.item(first.pid)['sensitive'], 'Force Stop is clickable for a window of one of our own processes')

    # Activating it must close the menu first and only then open the dialog.
    n.force_stop(first.pid)
    wait_until(lambda: n.state(first.pid)['dialog'], 'the confirmation dialog')
    time.sleep(0.5)
    current = n.state(first.pid)
    check(not current['menuOpen'] and current['modal'] == 1,
          'activating Force Stop closes the menu and leaves exactly one modal: the dialog')
    check('Process %d will be stopped' % first.pid in current['dialog']['description'],
          'the dialog names the process: %s' % current['dialog']['description'])
    n.shot('02-dialog.png')
    check(first.poll() is None, 'nothing is killed before the dialog is confirmed')

    n.xdo('key', 'Escape')
    wait_until(lambda: n.state(first.pid)['dialog'] is None and n.state(first.pid)['modal'] == 0,
               'the dismissed dialog')
    check(first.poll() is None, 'Escape dismisses the dialog and leaves the application running')
    n.shot('03-after-cancel.png')

    # Cinnamon keeps a stack of modal actors. If the dialog and the menu had overlapped, later menus
    # could no longer be opened or closed.
    labels = n.open_menu(first.pid)
    check(labels is not None, 'the menu opens again after the dialog')
    n.close_menu(first.pid)
    check(True, 'the menu closes again and no modal is left behind')

    n.force_stop(first.pid)
    n.confirm_dialog()
    status = exit_status(first)
    check(status == -9, 'confirming sends SIGKILL to the window owner (exit status %s)' % status)
    wait_until(lambda: not n.state(first.pid)['group'], 'the window list to drop the closed window')
    current = n.state(first.pid)
    check(current['dialog'] is None and current['modal'] == 0,
          'the dialog is gone and no modal is left behind after the kill')

    # Several windows of one application: the menu offers Close all, and Force Stop follows it.
    second, third = n.launch('Two'), n.launch('Three')
    wait_until(lambda: n.state(third.pid)['group'], 'the second window in the window list')
    time.sleep(1)
    labels = n.open_menu(third.pid)
    check(labels is not None and 'Close all' in labels and
          labels.index('Force Stop') == labels.index('Close all') + 1,
          'with several windows, Force Stop follows Close all: %s' % labels)
    n.shot('04-menu-several-windows.png')
    target = n.state(third.pid)['targetPid']
    victim, survivor = (second, third) if target == second.pid else (third, second)
    n.force_stop(third.pid)
    n.confirm_dialog()
    status = exit_status(victim)
    check(status == -9 and survivor.poll() is None,
          'Force Stop kills the process of the targeted window and leaves other windows alone')
    survivor.kill()

    # Windows that do not say which process owns them cannot be killed, so the item must not offer it.
    xmessage = n.launch('xmessage', 'xmessage', 'Force Stop test window')
    wait_until(lambda: n.state('xmessage')['group'], 'the xmessage window in the window list')
    n.open_menu('xmessage')
    if n.state('xmessage')['targetPid'] <= 0:
        check(not n.item('xmessage')['sensitive'], 'Force Stop is greyed out for a window without a PID')
    else:
        print('skip xmessage reports a PID, so the no-PID case is not exercised', flush=True)
    n.close_menu('xmessage')
    xmessage.kill()
    wait_until(lambda: not n.state('xmessage')['group'], 'the window list to drop the closed xmessage window')

    # The switches live on Cinnamon's own settings page. The screenshot is taken before anything changes.
    n.open_settings('06-settings.png', 'confirm=off')
    n.wait_setting('confirm', False)
    check(n.read_settings()['confirm'] is False,
          'the switch on the settings page reaches the running extension and its settings file')

    silent = n.launch('Silent')
    wait_until(lambda: n.state(silent.pid)['group'], 'the window to stop without confirmation')
    n.force_stop(silent.pid)
    status = exit_status(silent)
    current = n.state(silent.pid)
    check(status == -9 and current['dialog'] is None and current['modal'] == 0,
          'with the confirmation off, Force Stop stops the application at once')

    # Processes the application started: a child, which has a child of its own.
    tree, descendants = n.launch_with_children()
    wait_until(lambda: n.state(tree.pid)['group'], 'the window of a process that started others')
    n.force_stop(tree.pid)
    status = exit_status(tree)
    time.sleep(1)
    check(status == -9 and all(alive(pid) for pid in descendants),
          'by default, the processes the application started are left running')
    for pid in descendants:
        os.kill(pid, signal.SIGKILL)

    n.open_settings('-', 'kill-children=on')
    n.wait_setting('kill-children', True)
    control = n.launch('control', 'sleep', '600')
    tree, descendants = n.launch_with_children()
    wait_until(lambda: n.state(tree.pid)['group'], 'the window of a process that started others')
    n.force_stop(tree.pid)
    status = exit_status(tree)
    stopped = wait_until(lambda: not any(alive(pid) for pid in descendants),
                         'the processes the application started to be stopped')
    check(status == -9 and stopped and control.poll() is None,
          'with the setting on, the child and grandchild processes are stopped too, '
          'and an unrelated process is left alone')
    control.kill()

    n.open_settings('-', 'confirm=on')
    n.wait_setting('confirm', True)
    tree, descendants = n.launch_with_children()
    wait_until(lambda: n.state(tree.pid)['group'], 'the window of a process that started others')
    n.force_stop(tree.pid)
    wait_until(lambda: n.state(tree.pid)['dialog'], 'the confirmation dialog')
    time.sleep(0.4)
    description = n.state(tree.pid)['dialog']['description']
    check('processes it started' in description,
          'the dialog says that the started processes will be stopped too: %s' % description)
    n.shot('07-dialog-process-tree.png')
    n.xdo('key', 'Escape')
    wait_until(lambda: n.state(tree.pid)['dialog'] is None and n.state(tree.pid)['modal'] == 0,
               'the dismissed dialog')
    check(tree.poll() is None and all(alive(pid) for pid in descendants),
          'cancelling leaves the whole process tree running')
    for pid in descendants + [tree.pid]:
        if alive(pid):
            os.kill(pid, signal.SIGKILL)
    exit_status(tree)

    n.open_settings('-', 'kill-children=off', 'confirm=on')
    n.wait_setting('kill-children', False)
    check(n.read_settings() == {'confirm': True, 'kill-children': False},
          'the defaults can be restored from the settings page')

    # Disabling removes the item from menus that already exist and from menus created afterwards.
    fifth = n.launch('Five')
    wait_until(lambda: n.state(fifth.pid)['group'], 'the fifth window in the window list')
    n.open_menu(fifth.pid)
    n.close_menu(fifth.pid)
    n.ev('Extension.unloadExtension(UUID, Extension.Type.EXTENSION, false); return "ok";')
    time.sleep(0.5)
    labels = n.open_menu(fifth.pid)
    check(labels is not None and 'Force Stop' not in labels,
          'after disabling, an existing menu no longer lists Force Stop')
    n.close_menu(fifth.pid)
    fifth.kill()
    wait_until(lambda: not n.state(fifth.pid)['group'], 'the window list to drop the fifth window')
    sixth = n.launch('Six')
    wait_until(lambda: n.state(sixth.pid)['group'], 'the sixth window in the window list')
    labels = n.open_menu(sixth.pid)
    check(labels is not None and 'Force Stop' not in labels,
          'after disabling, a newly created menu does not list Force Stop')
    n.close_menu(sixth.pid)

    # Enabling again must also reach the menus that were created while it was off.
    n.ev('Extension.loadExtension(UUID, Extension.Type.EXTENSION); return "ok";')
    wait_until(lambda: n.ev('return String(!!extension());') == 'true', 'the re-enabled extension')
    labels = n.open_menu(sixth.pid)
    check(labels is not None and 'Force Stop' in labels,
          'after enabling, a menu that already existed lists Force Stop')
    n.close_menu(sixth.pid)

    # The window list builds a new menu class whenever it is reloaded.
    n.ev('Extension.reloadExtension(WINDOW_LIST, Extension.Type.APPLET); return "ok";')
    wait_until(lambda: n.ev('const group = groupFor(%d); return String(!!group && !group.rightClickMenu);' % sixth.pid)
               == 'true', 'the reloaded window list')
    labels = n.open_menu(sixth.pid)
    check(labels is not None and 'Force Stop' in labels,
          'after the window list is reloaded, its new menus list Force Stop')
    n.shot('05-menu-after-reload.png')
    n.close_menu(sixth.pid)


def javascript_errors(log):
    """Yields the JavaScript error blocks of a Cinnamon log. A block continues until a blank line."""
    block = []
    for line in log.splitlines():
        if 'JS ERROR' in line or 'JS LOG: [LookingGlass/error]' in line:
            if block:
                yield '\n'.join(block)
            block = [line]
        elif block:
            if not line.strip():
                yield '\n'.join(block)
                block = []
            else:
                block.append(line)
    if block:
        yield '\n'.join(block)


def main():
    missing = [tool for tool in ('Xephyr', 'xdotool', 'zenity', 'xmessage', 'import', 'cinnamon')
               if not shutil.which(tool)]
    if missing:
        raise Fail('missing tools: ' + ', '.join(missing))
    if not os.path.exists('/usr/share/cinnamon/cinnamon-settings/xlet-settings.py'):
        raise Fail('the settings page of Cinnamon (xlet-settings.py) was not found')

    nested = Nested()
    try:
        nested.start()
        run_tests(nested)
    finally:
        nested.stop()

    with open(nested.log_path) as handle:
        log = handle.read()
    for problem in ('incorrect pop', 'Wrong actor', 'Force Stop:'):
        check(problem not in log, 'the Cinnamon log has no "%s"' % problem)
    errors = list(javascript_errors(log))
    check(not any(UUID in error for error in errors),
          'the Cinnamon log has no JavaScript error in the extension')
    others = [error for error in errors if UUID not in error]
    print('info %d JavaScript error(s) elsewhere in the private Cinnamon, see %s'
          % (len(others), nested.log_path), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Fail as failure:
        check(False, str(failure))
    print('%d passed, %d failed. Screenshots and log: %s' % (RESULTS.count(True), RESULTS.count(False), ARTIFACTS))
    sys.exit(0 if RESULTS and all(RESULTS) else 1)
