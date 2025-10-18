import socket
import sys
import time
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.console import Console
import json
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

console = Console()

load_dotenv()  # Loads from .env by default

MQTT_SERVER = os.getenv("MQTT_SERVER")
MQTT_SERVER_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

MQTT_TOPIC =  "sensor/nmea2k"

GATEWAY_HOST = '192.168.1.53'
GATEWAY_PORT = 2000

# Initialize latest values
latest = {
    'latitude': None,
    'longitude': None,
    'engine_hours': None,
    'engine_temp': None,
    'fuel': None,
    'voltage': None,
    'heading': None,
    'depth_m': None,
    'depth_ft': None,
    'rpm': None,
    'time': None
}

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')
    
def log_to_mqtt(latitude,
                longitude,
                engine_hours,
                engine_temp,
                voltage,
                heading,
                depth_ft,
                engine_rpm
                ):
    clear_screen()
    table = Table(title="Engine Telemetry", show_header=False, box=None, pad_edge=False)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("UTC Time", latest.get("time", "--"))
    table.add_row("Engine Hours", f"{latest.get('engine_hours', '--')} hours")
    table.add_row("Engine Temp", f"{latest.get('engine_temp', '--')} °F")
    table.add_row("Fuel Level", f"{latest.get('fuel', '--')} L")
    table.add_row("Voltage", f"{latest.get('voltage', '--')} V")
    table.add_row("Engine RPM", f"{latest.get('rpm', '--')}")
    table.add_row("Heading", f"{latest.get('heading', '--')}° True")
    table.add_row("Depth", f"{latest.get('depth_m', '--')} m | {latest.get('depth_ft', '--')} ft")
    table.add_row("Latitude", latest.get("latitude", "--"))
    table.add_row("Longitude", latest.get("longitude", "--"))

    console.clear()
    console.print(table)

    print(f"Connecting to {MQTT_SERVER}:{MQTT_SERVER_PORT}")
    print(f"writing to MQTT topic {MQTT_TOPIC}")
    
    payload = {
    "latitude": f"{latitude}",
    "longitude": f"{longitude}",
    "engine_hours": f"{engine_hours}",
    "engine_temp": f"{engine_temp}",
    "heading": f"{heading}",
    "depth_ft": f"{depth_ft}",
    "engine_rpm": f"{engine_rpm}",
                }
    
    client = mqtt.Client()
    client.username_pw_set(MQTT_USERNAME,MQTT_PASSWORD)
    client.connect(MQTT_SERVER,int(MQTT_SERVER_PORT), 60)
    mqtt_result= client.publish(MQTT_TOPIC, json.dumps(payload))
    
    if mqtt_result.is_published:
        print(f"MQTT publish results -> {mqtt_result.rc}")
    
    print(f"MQTT publish done")
    time.sleep(30)
    
def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9 / 5) + 32
    
def meters_to_feet(m):
    """Convert Meters to Feet."""
    return round(m * 3.28084, 2)

def parse_line(line):
    if line.startswith('$YDXDR'):
        fields = line.split(',')
        print(f"fields0 -{fields}")
        if 'EngineHours#0' in line:
            try:
                latest['engine_hours'] = round(float(fields[2]), 2)
            except: pass
        elif 'Engine#0' in line and 'Fuel#0' in line and 'Alternator#0' in line:
            try:
                latest['engine_temp'] = celsius_to_fahrenheit(round(float(fields[2]), 1))
                latest['fuel'] = 1
                latest['voltage'] = round(float(fields[10]), 2)
            except: pass

    elif line.startswith('$YDHDG'):
        try:
            latest['heading'] = round(float(line.split(',')[1]), 1)
        except: pass

    elif line.startswith('$YDDBT'):
        try:
            depth = float(line.split(',')[1])
            latest['depth_m'] = round(float(line.split(',')[3]),2)
            latest['depth_ft'] = round(float(line.split(',')[1]), 3)
        except: pass

    elif line.startswith('$YDGGA'):
            try:
                latest['latitude'] = convert_latitude_to_dms( line.split(',')[2] + line.split(',')[3])
                latest['longitude'] = convert_longitude_to_dms( line.split(',')[4] + line.split(',')[5])
            except: pass
            
    elif line.startswith('$YDZDA'): 
            try:
                latest['time'] = parse_ydzda(line)
            except: pass

    elif line.startswith('$PCDIN') and '01F201' in line:
        try:
            hexdata = line.split(',')[4].strip().split('*')[0]
            rpm_bytes = bytes.fromhex(hexdata)
            if len(rpm_bytes) >= 7:
                rpm = int.from_bytes(rpm_bytes[3:7], byteorder='little') / 4.0
                latest['rpm'] = round(rpm, 1)
        except: pass

    log_to_mqtt(latest['latitude'],
                latest['longitude'],
                latest['engine_hours'],
                latest['engine_temp'],
                latest['voltage'] ,
                latest['heading'],
                latest['depth_ft'],
                latest['rpm'])

def listen_nmea2000():
    clear_screen()
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((GATEWAY_HOST, GATEWAY_PORT))
        print("Connected to NMEA 2000 stream...")
        while True:
            data = s.recv(1024)
            for line in data.decode(errors='ignore').splitlines():
                parse_line(line)
                
def convert_latitude_to_dms(lat_str):
    # Example input: "3309.4603N"
    direction = lat_str[-1]
    raw = lat_str[:-1]
    degrees = int(raw[:2])
    minutes_float = float(raw[2:])
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    result = f"{degrees}°{minutes:02d}'{seconds:.1f}\" {direction}"
    return result

def convert_longitude_to_dms(lon_str):
    # Example input: "09659.5216W"
    direction = lon_str[-1]
    raw = lon_str[:-1]
    degrees = int(raw[:3])
    minutes_float = float(raw[3:])
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    result = f"{degrees}°{minutes:02d}'{seconds:.1f}\" {direction}"
    return result

def parse_ydzda(sentence):
    # Example input: "$YDZDA,212636.03,12,10,2025,,*6A"
    parts = sentence.split(',')

    # Extract time and date components
    time_raw = parts[1]  # HHMMSS.ss
    day = int(parts[2])
    month = int(parts[3])
    year = int(parts[4])

    # Parse time
    hour = int(time_raw[:2])
    minute = int(time_raw[2:4])
    second = float(time_raw[4:])

    # Format output
    return f"UTC Time: {hour:02d}:{minute:02d}:{second:05.2f} on {year}-{month:02d}-{day:02d}"

if __name__ == "__main__":
    listen_nmea2000()
