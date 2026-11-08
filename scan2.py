import socket

HOST = '192.168.30.163'  # IP of the gateway
PORT = 2000           # TCP port

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((HOST, PORT))
    while True:
        data = s.recv(1024)
        print(data.decode(errors='ignore'))
