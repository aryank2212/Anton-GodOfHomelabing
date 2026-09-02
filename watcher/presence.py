import requests
import time
from datetime import datetime

URL = "http://localhost:8840/api/all"
HERMES_URL = "http://127.0.0.1:8002/event"

previous = {}


def publish(etype, severity, title, message=""):
    """Log locally and push to Hermes (which owns notifications)."""
    print(title)
    try:
        requests.post(
            HERMES_URL,
            json={
                "module": "watcher",
                "type": etype,
                "severity": severity,
                "title": title,
                "message": message,
                "tags": ["watcher", "lan", etype],
            },
            timeout=5,
        )
    except Exception as e:
        print(f"hermes publish failed: {e}")


while True:
    try:
        devices = requests.get(URL, timeout=10).json()

        current = {}

        for d in devices:
            mac = d["Mac"]
            online = bool(d["Now"])
            name = d["Name"] or d["IP"]

            current[mac] = online

            if mac not in previous:
                title = f"{name} discovered"
                publish(
                    "device.discovered",
                    "info",
                    title,
                    f"New device {name} ({mac}) appeared on the LAN.",
                )

            elif previous[mac] != online:
                state = "online" if online else "offline"
                publish(
                    f"device.{state}",
                    "info" if online else "warning",
                    f"{name} {state}",
                    f"{name} ({mac}) is now {state}.",
                )

        previous = current

    except Exception as e:
        print(e)

    time.sleep(10)
