"""openamika-iot starts only for someone who has accepted OpenAmika's IoT safety statement.

The statement is docs/IOT_DISCLAIMER.md in OpenAmika (English, Japanese, Simplified Chinese). OpenAmika's Tools page
shows it before IoT can be turned on and records the acceptance; when the brain then starts this server it passes the
accepted version in OPENAMIKA_IOT_DISCLAIMER_ACCEPTED. Run by hand (for example with --http on the machine that has
the Zigbee dongle), it needs --accept-iot-disclaimer <version>: giving it confirms that you have read and accept the
statement. A different version means the statement changed: read it again. Standard library only, so the refusal
works even before mcp or paho-mqtt are installed.
"""
from __future__ import annotations

import os

IOT_DISCLAIMER_VERSION = "2026-11-03"        # = IOT_DISCLAIMER_VERSION in OpenAmika's brain/iot_disclaimer.py
ENV_VAR = "OPENAMIKA_IOT_DISCLAIMER_ACCEPTED"
STATEMENT = "docs/IOT_DISCLAIMER.md\n  https://github.com/zeekk0/OpenAmika/blob/main/docs/IOT_DISCLAIMER.md"


def refusal(flag=None, environ=None):
    """None when the current statement was accepted (the --accept-iot-disclaimer value, else the environment variable
    the brain sets); otherwise the message to print before refusing to start."""
    environ = os.environ if environ is None else environ
    given = str(flag or environ.get(ENV_VAR) or "").strip()
    if given == IOT_DISCLAIMER_VERSION:
        return None
    head = ("openamika-iot did not start: " + (
        f"the IoT safety statement you accepted ({given}) is not the current one ({IOT_DISCLAIMER_VERSION})."
        if given else f"the IoT safety statement (version {IOT_DISCLAIMER_VERSION}) has not been accepted."))
    return (f"{head}\n"
            "Amika runs on AI models that can mishear, misunderstand or act on the wrong device at the wrong time;\n"
            "nothing guarantees that she will not cause harm. Everything you connect is at your own risk, and\n"
            "MekaHime Inc. is not responsible for the outcome. Never connect heaters, cooking appliances, locks,\n"
            "valves, alarms, medical equipment or anything else that could cause danger.\n"
            f"Read the whole statement: {STATEMENT}\n"
            "Accept it on OpenAmika's Tools page (OpenAmika then starts this server for you), or, when you run this\n"
            f"server yourself, pass --accept-iot-disclaimer {IOT_DISCLAIMER_VERSION} to confirm that you have read\n"
            "and accept it.")
