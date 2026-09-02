import os
import requests
import time
import psycopg2
from datetime import datetime

URL = os.environ.get("WATCHER_LAN_URL", "http://localhost:8840/api/all")
HERMES_URL = os.environ.get("HERMES_URL", "http://127.0.0.1:8002/event")

db = psycopg2.connect(
    host=os.environ.get("WATCHER_DB_HOST", "localhost"),
    port=int(os.environ.get("WATCHER_DB_PORT", "5438")),
    dbname=os.environ.get("WATCHER_DB_NAME", "watcher"),
    user=os.environ.get("WATCHER_DB_USER", "watcher"),
    password=os.environ.get("WATCHER_DB_PASSWORD", "change-me")
)

cur = db.cursor()

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
            ip = d["IP"]
            name = d["Name"] or ip
            online = bool(d["Now"])

            current[mac] = online

            cur.execute("""
            INSERT INTO devices(mac,name,ip,first_seen,last_seen,online)
            VALUES(%s,%s,%s,NOW(),NOW(),%s)
            ON CONFLICT(mac)
            DO UPDATE SET
                last_seen = NOW(),
                online = EXCLUDED.online,
                ip = EXCLUDED.ip
            """, (mac, name, ip, online))

            if mac not in previous:
                event = f"{name} discovered"
                cur.execute("""
                INSERT INTO events(mac,event,timestamp)
                VALUES(%s,%s,NOW())
                """, (mac, event))
                publish(
                    "device.discovered",
                    "info",
                    event,
                    f"New device {name} ({mac}, {ip}) appeared on the LAN.",
                )

            elif previous[mac] != online:
                state = "online" if online else "offline"
                cur.execute("""
                INSERT INTO events(mac,event,timestamp)
                VALUES(%s,%s,NOW())
                """, (mac, state))
                publish(
                    f"device.{state}",
                    "info" if online else "warning",
                    f"{name} {state}",
                    f"{name} ({mac}, {ip}) is now {state}.",
                )

        db.commit()
        previous = current

    except Exception as e:
        print(e)

    time.sleep(10)
