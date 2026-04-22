import carla
import time
import os
import csv
import numpy as np
import cv2

save_dir = "./Data/output"
os.makedirs(save_dir, exist_ok=True)
log_file = os.path.join(save_dir, "log.csv")

with open(log_file, mode='w', newline='') as file:
    csv.writer(file).writerow(["frame", "time", "x", "y", "z", "speed"])

client = carla.Client('localhost', 2000)
client.set_timeout(5.0)
world = client.get_world()

# ego 찾기 (role_name=ego)
ego_vehicle = None
for v in world.get_actors().filter('vehicle.*'):
    if v.attributes.get('role_name') == 'ego':
        ego_vehicle = v
        break

if ego_vehicle is None:
    raise RuntimeError("role_name='ego' 차량을 찾지 못했습니다.")

bp_library = world.get_blueprint_library()
camera_bp = bp_library.find('sensor.camera.rgb')
camera_bp.set_attribute('motion_blur_intensity', '0')
camera_bp.set_attribute('image_size_x', '800')
camera_bp.set_attribute('image_size_y', '600')
camera_bp.set_attribute('fov', '90')

camera_transform = carla.Transform(carla.Location(x=0, z=2.4))
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=ego_vehicle)
print("카메라 부착 완료:", camera)

# OpenCV 창 생성
win_name = "Ego Camera (press q to quit)"
cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)   # 크기 조절 가능
cv2.resizeWindow(win_name, 800, 600)

# 로그만 남기고, 이미지는 화면으로만 표시
def process_image(image):
    # 1) CARLA BGRA → NumPy 배열로 변환
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))

    # 2) BGR 추출 (마지막 알파 채널 제외)
    bgr = array[:, :, :3]

    # 3) OpenCV 창에 표시
    cv2.imshow(win_name, bgr)
    cv2.waitKey(1)   # 이벤트 처리 (1ms)

    # 4) 위치/속도 로그만 CSV에 기록 (원하면 생략 가능)
    transform = ego_vehicle.get_transform()
    velocity = ego_vehicle.get_velocity()
    speed = (velocity.x**2 + velocity.y**2 + velocity.z**2)**0.5
    with open(log_file, mode='a', newline='') as file:
        csv.writer(file).writerow([
            image.frame, image.timestamp,
            transform.location.x, transform.location.y, transform.location.z,
            speed
        ])

camera.listen(process_image)

try:
    while True:
        # q로 종료
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("사용자 종료(q)")
            break

        # ego가 사라지면 종료
        vehicles = world.get_actors().filter('vehicle.*')
        if ego_vehicle.id not in [v.id for v in vehicles]:
            print("Scenic 시뮬레이션 종료 - 차량이 사라짐")
            break

        time.sleep(0.01)
except KeyboardInterrupt:
    pass
finally:
    camera.stop()
    camera.destroy()
    cv2.destroyAllWindows()
    print("카메라 및 센서 종료 완료.")
