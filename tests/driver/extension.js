// Test-only companion for tests/nested_test.py. It evaluates JavaScript snippets inside the private
// Cinnamon so the orchestrator can inspect the shell. It must never be installed on a real desktop.
const Mainloop = imports.mainloop;
const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;

const DIR = GLib.getenv('FS_TEST_EVAL_DIR');
let timeoutId = 0;

function poll() {
    const dir = Gio.File.new_for_path(DIR);
    const requests = [];
    try {
        const children = dir.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = children.next_file(null)) !== null) {
            if (info.get_name().endsWith('.js')) requests.push(info.get_name());
        }
        children.close(null);
    } catch (error) {
        return true;
    }

    for (const name of requests) {
        const request = dir.get_child(name);
        let result;
        try {
            const [, contents] = GLib.file_get_contents(request.get_path());
            const code = String.fromCharCode.apply(null, contents);
            result = String(Function('imports', code)(imports));
        } catch (error) {
            result = '__ERROR__ ' + error + '\n' + (error.stack || '');
        }
        GLib.file_set_contents(request.get_path().slice(0, -3) + '.out', result);
        request.delete(null);
    }
    return true;
}

function init() {}
function enable() {
    GLib.mkdir_with_parents(DIR, 0o700);
    timeoutId = Mainloop.timeout_add(100, poll);
    GLib.file_set_contents(DIR + '/ready', '');
}
function disable() {
    if (timeoutId) Mainloop.source_remove(timeoutId);
    timeoutId = 0;
}
