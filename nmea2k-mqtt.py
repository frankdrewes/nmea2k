#!/usr/bin/env python3

import socket
import sys
import time
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.table import Table
from rich.console import Console
import json
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

console = Console(force_terminal=True)

load_dotenv()  # Loads from .env by default

MQTT_SERVER = os.getenv("MQTT_SERVER")
MQTT_SERVER_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
LISTENER_DURATION =  int(os.getenv("LISTENER_DURATION",30))

MQTT_TOPIC =  "sensor/nmea2k"
MQTT_LOCATION_TOPIC= "autodiscovery/device_tracker/boat_tracker/config"

GATEWAY_HOST = os.getenv("YD_Gateway_IP", "192.168.30.162")
GATEWAY_PORT = int(os.getenv("YD_Gateway_PORT", "2000"))

# Initialize latest values
latest = {
    'latitude': None,
    'longitude': None,
    'engine_hours': None,
    'engine_temp': None,
    'fuel': None,
    'fuel_tank_number': None,
    'voltage': None,
    'heading': None,
    'depth_m': None,
    'depth_ft': None,
    'rpm': None,
    'rate_of_turn': None,
    'pitch': None,
    'roll': None,
    'time': None
}

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')
    
def log_to_mqtt(latitude,
                longitude,
                engine_hours,
                engine_temp,
                heading,
                depth_ft,
                engine_rpm,
                voltage,
                fuel_level_percent,
                rate_of_turn,
                pitch,
                roll
                ):

    print(f"Connecting to {MQTT_SERVER}:{MQTT_SERVER_PORT}")
    print(f"writing to MQTT topic {MQTT_TOPIC}")
    
    payload = {
        "engine_hours": f"{engine_hours}",
        "engine_temp": f"{engine_temp}",
        "heading": f"{heading}",
        "depth_ft": f"{depth_ft}",
        "engine_rpm": f"{engine_rpm}",
        "bus_voltage": f"{voltage}",
        "fuel_level_percent": fuel_level_percent,
        "rate_of_turn_deg_per_min": rate_of_turn,
        "pitch_degrees": pitch,
        "roll_degrees": roll
                }
    
    location={
        "latitude": f"{latitude}",
        "longitude": f"{longitude}",
             }
    
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME,MQTT_PASSWORD)
    client.connect(MQTT_SERVER,int(MQTT_SERVER_PORT), 60)
    mqtt_result= client.publish(MQTT_TOPIC, json.dumps(payload))
    mqtt_result2 = client.publish(MQTT_LOCATION_TOPIC, json.dumps(location))
    
    if mqtt_result.is_published:
        print(f"MQTT #1 publish to {MQTT_SERVER}:{MQTT_SERVER_PORT} -> {mqtt_result.rc}")
    if mqtt_result2.is_published:
        print(f"MQTT #2 publish to {MQTT_SERVER}:{MQTT_SERVER_PORT} -> {mqtt_result2.rc}")
    
    print(f"MQTT publish done")
    
def build_panel(latest):
    lines = [
        f"UTC Time: {latest.get('time', '--')}",
        f"Engine Hours: {latest.get('engine_hours', '--')} hours",
        f"Engine Temp: {latest.get('engine_temp', '--')} °F",
        f"Fuel Level: {latest.get('fuel', '--')}% (Tank {latest.get('fuel_tank_number', '--')})",
        f"Voltage: {latest.get('voltage', '--')} V",
        f"Engine RPM: {latest.get('rpm', '--')}",
        f"Heading: {latest.get('heading', '--')}° True",
        f"Rate of Turn: {format_rate_of_turn(latest.get('rate_of_turn'))}",
        f"Pitch: {format_angle(latest.get('pitch'))}",
        f"Roll: {format_angle(latest.get('roll'))}",
        f"Depth: {latest.get('depth_m', '--')} m | {latest.get('depth_ft', '--')} ft",
        f"Latitude: {latest.get('latitude', '--')}",
        f"Longitude: {latest.get('longitude', '--')}",
    ]

    # Split into two columns
    midpoint = len(lines) // 2
    col1 = Text("\n".join(lines[:midpoint]))
    col2 = Text("\n".join(lines[midpoint:]))

    content = Columns([col1, col2], equal=True, expand=True)
    return Panel(content, title="Engine Telemetry", border_style="green", padding=(1, 2))

def format_rate_of_turn(rate):
    if rate is None:
        return "--"
    if rate < 0:
        return f"{abs(rate):.1f}°/min to port"
    if rate > 0:
        return f"{rate:.1f}°/min to starboard"
    return "0.0°/min"

def format_angle(angle):
    return "--" if angle is None else f"{angle:.2f}°"

def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit."""
    result= round( (celsius * 9 / 5) + 32,2) # to 2 decimal digits
    return result
    
def meters_to_feet(m):
    """Convert Meters to Feet."""
    return round(m * 3.28084, 2)

def decode_fluid_level(payload_hex):
    """Decode tank metadata and percentage from a PGN 127505 payload."""
    payload = bytes.fromhex(payload_hex)
    if len(payload) < 3:
        raise ValueError("PGN 127505 payload must contain at least 3 bytes")

    tank_instance = payload[0]
    fluid_type = payload[1] & 0x0F
    tank_number = (payload[1] >> 4) & 0x0F
    level_raw = (payload[2] << 8) | payload[1]

    return {
        "tank_instance": tank_instance,
        "fluid_type": fluid_type,
        "tank_number": tank_number,
        "level_raw": level_raw,
        "level_percent": level_raw * 0.004,
    }

def parse_line(line):
    if line.startswith('$YDXDR'):
        fields = line.split(',')
        for index in range(1, len(fields) - 3, 4):
            name = fields[index + 3].split('*')[0]
            if name in ('Pitch', 'Roll'):
                try:
                    latest[name.lower()] = round(float(fields[index + 1]), 2)
                except ValueError:
                    pass
        print(f"fields0 -{fields}")
        if 'EngineHours#0' in line:
            try:
                latest['engine_hours'] = round(float(fields[2]), 2)
            except: pass
        elif 'Engine#0' in line and 'Fuel#0' in line and 'Alternator#0' in line:
            try:
                latest['engine_temp'] = celsius_to_fahrenheit(round(float(fields[2]), 1))
                latest['voltage'] = round(float(fields[10]), 2)
            except: pass

    elif line.startswith('$YDHDG'):
        try:
            latest['heading'] = round(float(line.split(',')[1]), 1)
        except: pass

    elif line.startswith('$YDROT'):
        try:
            fields = line.split(',')
            if fields[2].split('*')[0] == 'A':
                latest['rate_of_turn'] = round(float(fields[1]), 1)
        except (IndexError, ValueError):
            pass

    elif line.startswith('$YDDBT'):
        try:
            depth = float(line.split(',')[1])
            latest['depth_m'] = round(float(line.split(',')[3]),2)
            latest['depth_ft'] = round(float(line.split(',')[1]), 3)
        except: pass

    elif line.startswith('$YDGGA'):
            try:
                #latest['latitude'] = convert_latitude_to_dms( line.split(',')[2] + line.split(',')[3])
                latest['latitude'] = line.split(',')[2] 
                #latest['longitude'] = convert_longitude_to_dms( line.split(',')[4] + line.split(',')[5])
                latest['longitude'] = line.split(',')[4] 
            except: pass
            
    elif line.startswith('$YDZDA'): 
            try:
                latest['time'] = parse_ydzda(line)
            except: pass

    elif line.startswith('$PCDIN') and '01F211' in line:
        try:
            hexdata = line.split(',')[4].strip().split('*')[0]
            fuel = decode_fluid_level(hexdata)
            latest['fuel'] = round(fuel['level_percent'], 1)
            latest['fuel_tank_number'] = fuel['tank_number']
        except (IndexError, ValueError):
            pass

    elif line.startswith('$MXPGN') and '01F211' in line:
        try:
            hexdata = line.split(',')[3].strip().split('*')[0]
            fuel = decode_fluid_level(hexdata)
            latest['fuel'] = round(fuel['level_percent'], 1)
            latest['fuel_tank_number'] = fuel['tank_number']
        except (IndexError, ValueError):
            pass

    elif line.startswith('$PCDIN') and '01F201' in line:
        try:
            hexdata = line.split(',')[4].strip().split('*')[0]
            rpm_bytes = bytes.fromhex(hexdata)
            if len(rpm_bytes) >= 7:
                rpm = int.from_bytes(rpm_bytes[3:7], byteorder='little') / 4.0
                latest['rpm'] = round(rpm, 1)
        except: pass

def listen_nmea2000():
    timeout = 10  # seconds
    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("⏳ Listening to NMEA 2000...", total=LISTENER_DURATION)
        start_time = time.time()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((GATEWAY_HOST, GATEWAY_PORT))
            print(f"Connected to NMEA 2000 stream at {GATEWAY_HOST}:{GATEWAY_PORT}")
            while time.time() - start_time < LISTENER_DURATION:
                data = s.recv(1024)
                #print(data.decode(errors='ignore'))  # or parse_line(data)
                progress.advance(task, 1)
                time.sleep(1)  # ensures 1-second pacing
        print(f"✅ Stream closed after {LISTENER_DURATION} seconds.")

        # Now decode
        for line in data.decode(errors='ignore').splitlines():
            parse_line(line)
            
        # Print Panel with results
        console.print(build_panel(latest))

        # Now write to MQTT
        log_to_mqtt(latest['latitude'],
                latest['longitude'],
                latest['engine_hours'],
                latest['engine_temp'],
                latest['heading'],
                latest['depth_ft'],
                latest['rpm'],
                latest['voltage'],
                latest['fuel'],
                latest['rate_of_turn'],
                latest['pitch'],
                latest['roll']
                )

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
    clear_screen()
    listen_nmea2000()
