# scripts/nwws_monitor.py
import logging
import os
import sys
from dotenv import load_dotenv
from slixmpp import ClientXMPP

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

NWWS_USER = os.getenv("NWWS_USER")
NWWS_PASSWORD = os.getenv("NWWS_PASSWORD")
NWWS_SERVER = "nwws-oi.weather.gov"

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("nwws_monitor")

class NWWSMonitorClient(ClientXMPP):
    def __init__(self, jid, password):
        super().__init__(jid, password)
        self.add_event_handler("session_start", self.session_start)
        self.add_event_handler("message", self.message)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("failed_auth", self.failed_auth)

    def session_start(self, event):
        logger.info("Connected to NWWS-OI. Listening for products...")
        self.send_presence()
        self.get_roster()

    def on_disconnect(self, event):
        logger.warning("Disconnected from NWWS.")

    def failed_auth(self, event):
        logger.error("Authentication failed!")

    def message(self, msg):
        if msg['type'] in ('chat', 'normal'):
            body = msg['body']
            if not body: return
            
            # Print first 500 chars clearly
            print("\n" + "="*80)
            print(f"SENDER: {msg['from']}")
            print("-" * 40)
            print(body[:500])
            print("="*80 + "\n")

if __name__ == "__main__":
    if not NWWS_USER or not NWWS_PASSWORD:
        print("Error: NWWS_USER or NWWS_PASSWORD not set in .env")
        sys.exit(1)

    jid = f"{NWWS_USER}@{NWWS_SERVER}"
    print(f"Connecting to {NWWS_SERVER} as {jid}...")
    
    # Use the standard synchronous-entry pattern for the test script
    xmpp = NWWSMonitorClient(jid, NWWS_PASSWORD)
    xmpp.use_ipv6 = False
    
    # Connect and run the loop
    if xmpp.connect(address=(NWWS_SERVER, 5222)):
        print("Starting event loop. Press Ctrl+C to stop.")
        xmpp.process(forever=True)
    else:
        print("Failed to initiate connection.")
