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

MQTT_TOPIC =  "sensor/nmea2k"
MQTT_LOCATION_TOPIC= "autodiscovery/device_tracker/boat_tracker/config"

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

    print(f"Connecting to {MQTT_SERVER}:{MQTT_SERVER_PORT}")
    print(f"writing to MQTT topic {MQTT_TOPIC}")
    
    payload = {
    "engine_hours": f"{engine_hours}",
    "engine_temp": f"{engine_temp}",
    "heading": f"{heading}",
    "depth_ft": f"{depth_ft}",
    "engine_rpm": f"{engine_rpm}",
                }
    
    location={
    "latitude": f"{latitude}",
    "longitude": f"{longitude}",
                }
    
    client = mqtt.Client()
    client.username_pw_set(MQTT_USERNAME,MQTT_PASSWORD)
    client.connect(MQTT_SERVER,int(MQTT_SERVER_PORT), 60)
    mqtt_result= client.publish(MQTT_TOPIC, json.dumps(payload))
    mqtt_result2 = client.publish(MQTT_LOCATION_TOPIC, json.dumps(location))
    
    if mqtt_result.is_published:
        print(f"MQTT #1 publish results -> {mqtt_result.rc}")
    if mqtt_result2.is_published:
        print(f"MQTT #2 publish results -> {mqtt_result2.rc}")
    
    print(f"MQTT publish done")
    
def build_panel(latest):
    lines = [
        f"UTC Time: {latest.get('time', '--')}",
        f"Engine Hours: {latest.get('engine_hours', '--')} hours",
        f"Engine Temp: {latest.get('engine_temp', '--')} °F",
        f"Fuel Level: {latest.get('fuel', '--')} L",
        f"Voltage: {latest.get('voltage', '--')} V",
        f"Engine RPM: {latest.get('rpm', '--')}",
        f"Heading: {latest.get('heading', '--')}° True",
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
                #latest['latitude'] = convert_latitude_to_dms( line.split(',')[2] + line.split(',')[3])
                latest['latitude'] = line.split(',')[2] 
                #latest['longitude'] = convert_longitude_to_dms( line.split(',')[4] + line.split(',')[5])
                latest['longitude'] = line.split(',')[4] 
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

def listen_nmea2000():
    timeout = 10  # seconds
    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("⏳ Listening to NMEA 2000...", total=timeout)
        start_time = time.time()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((GATEWAY_HOST, GATEWAY_PORT))
            print("Connected to NMEA 2000 stream...")
            while time.time() - start_time < timeout:
                data = s.recv(1024)
                #print(data.decode(errors='ignore'))  # or parse_line(data)
                progress.advance(task, 1)
                time.sleep(1)  # ensures 1-second pacing
        print("✅ Stream closed after 30 seconds.")

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
                latest['voltage'] ,
                latest['heading'],
                latest['depth_ft'],
                latest['rpm'])

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
