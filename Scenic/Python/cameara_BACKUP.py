import carla
import time
import os

# === 1. 저장 폴더 준비 ===
save_dir = "vv/Scenic/Data/output_images"
os.makedirs(save_dir, exist_ok=True)

# === 2. CARLA 서버 연결 ===
client = carla.Client('localhost', 2000)
client.set_timeout(5.0)
world = client.get_world()

# === 3. Scenic이 spawn한 차량 찾기 ===
ego_vehicle = None
for _ in range(100):  # 최대 10초까지 기다림
    vehicles = world.get_actors().filter('vehicle.*')
    if len(vehicles) > 0:
        ego_vehicle = vehicles[0]
        print("차량 찾음:", ego_vehicle)
        break
    time.sleep(1)

if ego_vehicle is None:
    raise RuntimeError("Scenic이 spawn한 차량을 찾지 못했습니다.")

# === 4. 카메라(sensor.camera.rgb) blueprint 준비 ===
bp_library = world.get_blueprint_library()
camera_bp = bp_library.find('sensor.camera.rgb')
camera_bp.set_attribute('image_size_x', '800')
camera_bp.set_attribute('image_size_y', '600')
camera_bp.set_attribute('fov', '90')

# === 5. 차량에 카메라 부착 ===
camera_transform = carla.Transform(carla.Location(x=1.5, z=2.4))  # 차량 기준 위치
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=ego_vehicle)
print("카메라 부착 완료:", camera)

# === 6. 카메라 콜백 설정 (이미지 파일 저장) ===
def process_image(image):
    image_filename = f"{save_dir}/frame_{image.frame:04d}.png"
    image.save_to_disk(image_filename)
    print(f"저장됨: {image_filename}")

camera.listen(lambda image: process_image(image))

# === 7. 시뮬레이션 유지 ===
try:
    while True:
        vehicles = world.get_actors().filter('vehicle.*')
        if ego_vehicle.id not in [v.id for v in vehicles]:
            print("Scenic 시뮬레이션 종료 - 차량이 사라짐")
            break
        time.sleep(0.5)
except KeyboardInterrupt:
    pass

# === 8. 종료 처리 ===
camera.stop()
camera.destroy()
print("카메라 및 센서 종료 완료.")
