import carla
import json
import os
import time

# # 1. JSON 파일 불러오기
# with open("vv/Data/records.json") as f:
#     records = json.load(f)

# scene = records[0]["ego_data"]
# position = scene[0]     # [x, y, z]
# heading = scene[2]      # radians → degrees

# 2. CARLA 연결
client = carla.Client("localhost", 2000)
client.set_timeout(10.0)
world = client.load_world("Town01")
blueprints = world.get_blueprint_library()

# 3. 차량 스폰
vehicle_bp = blueprints.find("vehicle.tesla.model3")
spawn_transform = carla.Transform(
    carla.Location(x=92, y=2, z=10.2),
    carla.Rotation(yaw=2 * 180 / 3.1416)
)
vehicle = world.spawn_actor(vehicle_bp, spawn_transform)

# 4. 카메라 부착
camera_bp = blueprints.find("sensor.camera.rgb")
camera_bp.set_attribute("image_size_x", "800")
camera_bp.set_attribute("image_size_y", "600")
camera_bp.set_attribute("fov", "90")

camera_transform = carla.Transform(carla.Location(x=1.5, z=2.4))
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=vehicle)

# 5. 이미지 저장
save_dir = "vv/Data/output_images"
os.makedirs(save_dir, exist_ok=True)
save_path = os.path.join(save_dir, "scene_0002.png")

def save_image(image):
    image.save_to_disk(save_path)
    print(f"📷 Saved: {save_path}")

camera.listen(save_image)

# 6. 시뮬레이션 진행
time.sleep(1.0)
world.tick()
time.sleep(0.5)

# 7. 종료
camera.stop()
camera.destroy()
vehicle.destroy()
