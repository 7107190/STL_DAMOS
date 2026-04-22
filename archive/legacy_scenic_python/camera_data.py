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
client.set_timeout(5.0)
world = client.get_world()

# === 차량 찾기 ===
# ego_vehicle = None
# for _ in range(1000):
#     vehicles = world.get_actors().filter('vehicle.*')
#     if len(vehicles) > 0:
#         print("차량 이름:", vehicles[0].id)
#         ego_vehicle = vehicles[0]
#         print("차량 찾음:", ego_vehicle)
#         print(vehicles)
#         settings = world.get_settings()
#         print("synchronous_mode =", settings.synchronous_mode)
#         break
#     time.sleep(1)

# if ego_vehicle is None:
#     raise RuntimeError("Scenic이 spawn한 차량을 찾지 못했습니다.")

ego_vehicle = None
for v in world.get_actors().filter('vehicle.*'):
    if v.attributes.get('role_name') == 'ego':
        ego_vehicle = v
        break

if ego_vehicle is None:
    raise RuntimeError("role_name='ego' 차량을 찾지 못했습니다.")

# === 카메라 설정 ===
bp_library = world.get_blueprint_library()
camera_bp = bp_library.find('sensor.camera.rgb')
camera_bp.set_attribute('motion_blur_intensity', '0') # 모션 블러 비활성화
camera_bp.set_attribute('image_size_x', '800')
camera_bp.set_attribute('image_size_y', '600')
camera_bp.set_attribute('fov', '90')

camera_transform = carla.Transform(carla.Location(x=0, z=2.4))
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=ego_vehicle)
print("카메라 부착 완료:", camera)

# === 이미지 콜백 ===
def process_image(image):
    # 이미지 저장
    image_filename = f"{save_dir}/frame_{image.frame:04d}.png"
    image.save_to_disk(image_filename)

    # 차량 위치와 속도 가져오기
    transform = ego_vehicle.get_transform()
    velocity = ego_vehicle.get_velocity()
    speed = (velocity.x**2 + velocity.y**2 + velocity.z**2)**0.5

    # 로그 저장
    with open(log_file, mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([
            image.frame,
            image.timestamp,
            transform.location.x,
            transform.location.y,
            transform.location.z,
            
            speed
        ])
    print(f"저장됨: {image_filename}  위치=({transform.location.x:.1f},{transform.location.y:.1f}) 속도={speed:.2f}")

camera.listen(lambda image: process_image(image))

# === 시뮬레이션 유지 ===
try:
    while True:
        vehicles = world.get_actors().filter('vehicle.*')
        if ego_vehicle.id not in [v.id for v in vehicles]:
            print("Scenic 시뮬레이션 종료 - 차량이 사라짐")
            break
        time.sleep(0.5)
except KeyboardInterrupt:
    pass

# === 종료 처리 ===
camera.stop()
camera.destroy()
print("카메라 및 센서 종료 완료.")
