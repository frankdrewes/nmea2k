import socket
import struct

# Gateway connection settings
HOST = '192.168.1.53'  # Replace with your gateway IP
PORT = 2000            # TCP port for NMEA 2000 stream

def parse_pgn_128267(data):
    # PGN 128267: Water Depth
    # Format: [SID, Depth, Offset, Range]
    # Depth is a 4-byte float at offset 1 (in meters)
    if len(data) < 5:
        return None
    depth_raw = struct.unpack_from('<f', data, 1)[0]
    return round(depth_raw, 2)

def listen_for_depth():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        print("Connected to NMEA 2000 stream...")
        while True:
            packet = s.recv(1024)
            if not packet:
                continue
            # Look for PGN 128267 (0x1F00B)
            if b'\x00\x0B\x1F' in packet:
                depth = parse_pgn_128267(packet)
                if depth:
                    print(f"Water Depth: {depth} meters")

if __name__ == "__main__":
    listen_for_depth()
