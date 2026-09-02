import requests
import time

URL = "http://localhost:8840/api/all"

while True:
    try:
        r = requests.get(URL, timeout=10)
        devices = r.json()

        print("=" * 70)

        for d in devices:
            status = "ONLINE" if d.get("Now") else "OFFLINE"

            print(
                f'{d.get("IP"):15} '
                f'{d.get("Mac"):20} '
                f'{d.get("Name") or "Unknown":20} '
                f'{status}'
            )

    except Exception as e:
        print(e)

    time.sleep(30)
