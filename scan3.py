import socket

HOST = '192.168.1.53'  # Updated IP of the gateway
PORT = 2000            # Updated TCP port

def meters_to_feet(meters):
    return round(meters * 3.28084, 2)

def parse_line(line):
    if line.startswith('$YDXDR'):
        fields = line.split(',')
        if 'EngineHours#0' in line:
            try:
                hours = float(fields[2])
                print(f"Engine Hours: {hours:.2f} h")
            except:
                pass
        elif 'Engine#0' in line and 'Fuel#0' in line and 'Alternator#0' in line:
            try:
                temp = float(fields[2])
                #fuel = float(fields[5])
                voltage = float(fields[10])
                print(f"Engine Temp: {temp:.1f} °C | Voltage: {voltage:.2f} V")
            except:
                pass

    elif line.startswith('$YDHDG'):
        try:
            heading = float(line.split(',')[1])
            print(f"Heading: {heading:.1f}° True")
        except:
            pass

    elif line.startswith('$YDDPT'):
        try:
            depth_m = float(line.split(',')[1])
            depth_ft = meters_to_feet(depth_m)
            print(f"Depth: {depth_m:.2f} m | {depth_ft:.2f} ft")
        except:
            pass

    elif line.startswith('$PCDIN') and '01F201' in line:
        # PGN 127488: Engine RPM
        try:
            hexdata = line.split(',')[4].strip().split('*')[0]
            rpm_bytes = bytes.fromhex(hexdata)
            if len(rpm_bytes) >= 7:
                rpm = int.from_bytes(rpm_bytes[3:7], byteorder='little') / 4.0
                print(f"Engine RPM: {rpm:.1f}")
        except:
            pass

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
