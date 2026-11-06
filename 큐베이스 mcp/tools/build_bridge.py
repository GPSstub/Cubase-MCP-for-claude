"""Builds cubase/mcp_bridge.js from the template with every Cubase key command, and
writes server/commands.json with the same order. Re-run after changing key commands in Cubase.

Usage: python tools/build_bridge.py [--install]
  --install  also copy the built script into Cubase's MIDI Remote Local scripts folder
"""

import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_COMMANDS = os.path.join(os.environ["APPDATA"], "Steinberg", "Cubase 13_64", "Key Commands.xml")
INSTALL_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Steinberg", "Cubase", "MIDI Remote",
                           "Driver Scripts", "Local", "mcp", "bridge")
MARKER = "/*@@COMMANDS@@*/[['Transport', 'Start'], ['Transport', 'Stop']]"


def read_commands(path):
    root = ET.parse(path).getroot()
    out = []
    for cat in root.find("list[@name='Categories']"):
        cat_name = cat.find("string[@name='Name']").get("value")
        cmds = cat.find("list[@name='Commands']")
        if cmds is None:
            continue
        for item in cmds:
            name = item.find("string[@name='Name']")
            if name is not None:
                out.append([cat_name, name.get("value")])
    return out


def main():
    commands = read_commands(KEY_COMMANDS)
    with open(os.path.join(ROOT, "cubase", "mcp_bridge.template.js"), encoding="utf-8") as f:
        template = f.read()
    if MARKER not in template:
        sys.exit("command marker not found in template")
    js = template.replace(MARKER, json.dumps(commands, ensure_ascii=True))
    out_js = os.path.join(ROOT, "cubase", "mcp_bridge.js")
    with open(out_js, "w", encoding="utf-8") as f:
        f.write(js)
    with open(os.path.join(ROOT, "server", "commands.json"), "w", encoding="utf-8") as f:
        json.dump(commands, f, ensure_ascii=False, indent=0)
    print("%d commands -> %s" % (len(commands), out_js))
    if "--install" in sys.argv:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        shutil.copy(out_js, INSTALL_DIR)
        print("installed -> " + INSTALL_DIR)


if __name__ == "__main__":
    main()
