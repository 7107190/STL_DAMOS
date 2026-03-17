import carla
import time
import math

# trajectory: [(time, x, y, yaw)]
trajectory = [
    (0.0, 10.0, 20.0, 0.0),
    (1.0, 20.0, 20.0, 0.0),
    (2.0, 30.0, 20.0, 0.0),
    (3.0, 40.0, 20.0, 0.0)
]

# carla world, vehicle 생성 (생략)
# ...

for target_time, target_x, target_y, target_yaw in trajectory:
    start_time = time.time()
    while time.time() - start_time < target_time:
        # 현재 위치 얻기
        transform = vehicle.get_transform()
        loc = transform.location

        # 간단하게: x 차이만 보고 steer 결정
        error_x = target_x - loc.x
        steer = max(-1.0, min(1.0, error_x * 0.1))
        
        # throttle: 시간에 따라 강제
        throttle = 0.5
        vehicle.apply_control(carla.VehicleControl(throttle=throttle, steer=steer))
        time.sleep(0.05)
