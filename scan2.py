import socket
import os
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("YD_Gateway_IP", "192.168.30.162")
PORT = int(os.getenv("YD_Gateway_PORT", "2000"))

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((HOST, PORT))
    while True:
        data = s.recv(1024)
        print(data.decode(errors='ignore'))
