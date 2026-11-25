#!/usr/bin/env python3
"""Regenerates the translation template force-stop@enisn/po/force-stop@enisn.pot.

It covers what Cinnamon's own cinnamon-xlet-makepot covers for this extension: the strings marked
with _() in extension.js, and the descriptions and tooltips of settings-schema.json and metadata.json.
That tool needs python3-polib, which is not installed everywhere.

Usage: python3 tools/make_pot.py
"""
import json
import os
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
XLET = ROOT / 'force-stop@enisn'
TARGET = XLET / 'po' / 'force-stop@enisn.pot'


def entry(comment, text):
    return '#. %s\nmsgid "%s"\nmsgstr ""\n\n' % (comment, text.replace('\\', '\\\\').replace('"', '\\"'))


def main():
    with tempfile.TemporaryDirectory() as temporary:
        extracted = pathlib.Path(temporary) / 'code.pot'
        subprocess.run([
            'xgettext', '--from-code=UTF-8', '--language=JavaScript', '-k_',
            '--package-name=force-stop@enisn',
            '--package-version=%s' % json.loads((XLET / 'metadata.json').read_text())['version'],
            '--msgid-bugs-address=https://github.com/linuxmint/cinnamon-spices-extensions/issues',
            '-o', str(extracted), 'extension.js'], cwd=XLET, check=True, env=dict(os.environ, TZ='UTC'))
        extracted = extracted.read_text()

    # The name is used in the code as well, so its entry only gains a second comment.
    extracted, replaced = re.subn(r'(#: [^\n]*\n)(msgid "Force Stop"\n)',
                                  r'#. metadata.json->name\n\1\2', extracted)
    assert replaced == 1
    body = extracted[extracted.index('\n#: '):].lstrip('\n')

    schema = json.loads((XLET / 'settings-schema.json').read_text())
    for key, setting in schema.items():
        for field in ('description', 'tooltip'):
            if field in setting:
                body += entry('settings-schema.json->%s->%s' % (key, field), setting[field])
        if 'options' in setting:
            for label, value in setting['options'].items():
                if value != 'custom':
                    body += entry('settings-schema.json->%s->options' % key, label)
    metadata = json.loads((XLET / 'metadata.json').read_text())
    body += entry('metadata.json->description', metadata['description'])

    created = re.search(r'"POT-Creation-Date: ([^"\\]*)', extracted).group(1)
    header = '''# Force Stop for Cinnamon.
# Copyright (C) 2026 enisn
# This file is distributed under the same license as the force-stop@enisn package.
# enisn, 2026.
#
msgid ""
msgstr ""
"Project-Id-Version: force-stop@enisn %(version)s\\n"
"Report-Msgid-Bugs-To: https://github.com/linuxmint/cinnamon-spices-extensions/issues\\n"
"POT-Creation-Date: %(created)s\\n"
"PO-Revision-Date: \\n"
"Last-Translator: \\n"
"Language-Team: \\n"
"Language: \\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"

''' % {'version': metadata['version'], 'created': created}
    TARGET.write_text(header + body)
    print('Wrote %s' % TARGET.relative_to(ROOT))


if __name__ == '__main__':
    main()
