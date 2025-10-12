import os
import time

def read_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_c = int(f.read()) / 1000.0
        temp_f = (temp_c * 9/5) + 32
        return round(temp_c, 2), round(temp_f, 2)
    except:
        return None, None

def get_color(temp_c):
    if temp_c <= 50:
        return "\033[92m"  # Green
    elif temp_c <= 70:
        return "\033[93m"  # Yellow
    else:
        return "\033[91m"  # Red

def display_cpu_temp():
    temp_c, temp_f = read_cpu_temp()
    print("📡 Raspberry Pi CPU Temperature Monitor")
    print("--------------------------------------")
    if temp_c is not None:
        color = get_color(temp_c)
        reset = "\033[0m"
        print(f"{color}CPU Temp: {temp_c} °C | {temp_f} °F{reset}")
    else:
        print("Unable to read CPU temperature")

if __name__ == "__main__":
    while True:
        os.system('clear')
        display_cpu_temp()
        time.sleep(5)
