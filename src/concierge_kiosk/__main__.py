import os

import uvicorn

if __name__ == "__main__":
    # Containers/appliances serve the kiosk and staff consoles on the LAN; set
    # CONCIERGE_BIND_HOST=127.0.0.1 for a single-device kiosk.
    uvicorn.run("concierge_kiosk.main:app",
                host=os.getenv("CONCIERGE_BIND_HOST", "0.0.0.0"),  # nosec B104  # documented appliance default
                port=int(os.getenv("CONCIERGE_BIND_PORT", "8000")))
