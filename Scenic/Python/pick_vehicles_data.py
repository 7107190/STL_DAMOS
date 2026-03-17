import carla
import time
import os
import csv

# === 저장 폴더 및 로그 파일 준비 ===
save_dir = "./Data/output"
os.makedirs(save_dir, exist_ok=True)
log_file = os.path.join(save_dir, "log.csv")

# CSV 로그 헤더 작성
with open(log_file, mode='w', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(["frame", "time", "x", "y", "z", "speed"])

# === CARLA 서버 연결 ===
client = carla.Client('localhost', 2000)
client.set_timeout(1.0)
world = client.get_world()

# === 차량 찾기 ===
vehicles = world.get_actors().filter('vehicle.*')
for v in vehicles:
    print(v.id, v.type_id, v.attributes)