NMEA 2000 live monitor

This project reads boat telemetry from a Yacht Devices WiFi gateway and renders a live terminal dashboard with the latest engine, electrical, and navigation data.

Files:
- scan.py - early version
- scan2.py - raw data stream dump
- scan3.py - decoder experiments
- scan4.py - live display dashboard

Run the app:

python scan4.py --host 192.168.30.163 --port 2000 --seconds 60

Optional environment variables:
- GATEWAY_HOST=192.168.30.163
- GATEWAY_PORT=2000
- LISTENER_SECONDS=60
