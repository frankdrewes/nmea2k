import socket
import sys

HOST = '192.168.1.53'
PORT = 2000

# Initialize latest values
latest = {
    'engine_hours': None,
    'engine_temp': None,
    'fuel': None,
    'voltage': None,
    'heading': None,
    'depth_m': None,
    'depth_ft': None,
    'rpm': None,
}

def meters_to_feet(m):
    return round(m * 3.28084, 2)

def print_status():
    sys.stdout.write("\033[H\033[J")  # Clear screen
    print(f"Engine Hours   : {latest['engine_hours'] or '--'} h")
    print(f"Engine Temp    : {latest['engine_temp'] or '--'} °C")
    print(f"Fuel Level     : {latest['fuel'] or '--'} L")
    print(f"Voltage        : {latest['voltage'] or '--'} V")
    print(f"Engine RPM     : {latest['rpm'] or '--'}")
    print(f"Heading        : {latest['heading'] or '--'}° True")
    print(f"Depth          : {latest['depth_m'] or '--'} m | {latest['depth_ft'] or '--'} ft")
    print("-" * 40)

def parse_line(line):
    if line.startswith('$YDXDR'):
        fields = line.split(',')
        if 'EngineHours#0' in line:
            try:
                latest['engine_hours'] = round(float(fields[2]), 2)
            except: pass
        elif 'Engine#0' in line and 'Fuel#0' in line and 'Alternator#0' in line:
            try:
                latest['engine_temp'] = round(float(fields[2]), 1)
                latest['fuel'] = 1
                latest['voltage'] = round(float(fields[10]), 2)
            except: pass

    elif line.startswith('$YDHDG'):
        try:
            latest['heading'] = round(float(line.split(',')[1]), 1)
        except: pass

    elif line.startswith('$YDDPT'):
        try:
            depth_m = float(line.split(',')[1])
            latest['depth_m'] = round(depth_m, 2)
            latest['depth_ft'] = meters_to_feet(depth_m)
        except: pass

    elif line.startswith('$PCDIN') and '01F201' in line:
        try:
            hexdata = line.split(',')[4].strip().split('*')[0]
            rpm_bytes = bytes.fromhex(hexdata)
            if len(rpm_bytes) >= 7:
                rpm = int.from_bytes(rpm_bytes[3:7], byteorder='little') / 4.0
                latest['rpm'] = round(rpm, 1)
        except: pass

    print_status()

def listen_nmea2000():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        print("Connected to NMEA 2000 stream...")
        while True:
            data = s.recv(1024)
            for line in data.decode(errors='ignore').splitlines():
                parse_line(line)

if __name__ == "__main__":
    listen_nmea2000()
