NMEA 2000 live monitor

This project reads boat telemetry from a Yacht Devices WiFi gateway and renders a live terminal dashboard with the latest engine, electrical, and navigation data.

Files:
- scan.py - early version
- scan2.py - raw data stream dump
- scan3.py - decoder experiments
- scan4.py - live display dashboard

Send fixed battery test data (YDWG-02 RAW TCP):

```text
YD_Gateway_IP=192.168.30.162
YD_Gateway_PORT_RAW=3000
YD_Gateway_SOURCE_ADDRESS=64
```

Choose an unused NMEA 2000 source address for testing. Preview the four frames
without sending:

```text
python send_fixed_nmea_battery.py --source-address 0x40
```

To transmit both battery PGNs for both instances every 15 seconds, add
`--transmit`. This sends synthetic readings onto the NMEA 2000 network; use
only on an isolated test network.

Run the app:

python scan4.py --host 192.168.30.163 --port 2000 --seconds 60

Optional environment variables:
- GATEWAY_HOST=192.168.30.163
- GATEWAY_PORT=2000
- LISTENER_SECONDS=60
