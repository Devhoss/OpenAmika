"""Re-applicable patch for zigbee-herdsman's ember adapter on Windows.

Node serialport opens a COM port with DTR asserted and RTS disabled. On the SONOFF Zigbee Dongle Plus MG24
the CP210x's DTR/RTS pins drive the EFR32's reset/boot lines, and that exact state boots the dongle into its
Gecko bootloader - zigbee2mqtt then waits for an ASH RSTACK that never comes (probed on COM3, 2026-10-24:
DTR high + RTS low answers with the bootloader menu; DTR low + RTS low answers RSTACK). Opening the port with
`hupcl: false` releases DTR, so the dongle resets into its Zigbee firmware and answers.

Run after (re)installing zigbee2mqtt:   python patch_herdsman_windows.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent / "zigbee2mqtt" / "node_modules" / ".pnpm"
NEEDLE = "                xon: false,\n                xoff: false,\n            };"
PATCHED = ("                xon: false,\n                xoff: false,\n"
           "                hupcl: false, // OpenAmika: release DTR on open - the MG24 dongle boots into its bootloader otherwise (Windows)\n"
           "            };")


def main() -> int:
    hits = list(ROOT.glob("zigbee-herdsman@*/node_modules/zigbee-herdsman/dist/adapter/ember/uart/ash.js"))
    if not hits:
        print("zigbee-herdsman ash.js not found - is zigbee2mqtt installed?")
        return 1
    for path in hits:
        text = path.read_text(encoding="utf-8")
        if "hupcl: false" in text:
            print("already patched:", path)
            continue
        if NEEDLE not in text:
            print(f"unexpected ash.js layout in {path}; patch by hand (add hupcl: false to serialOpts)")
            return 1
        path.write_text(text.replace(NEEDLE, PATCHED), encoding="utf-8")
        print("patched:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
