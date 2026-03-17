import carla
import random
import pygame
import numpy as np
import math

def game_loop():
    # 0. Pygame 및 기본 변수 초기화
    pygame.init()
    actor_list = []
    width, height = 800, 600
    display = pygame.display.set_mode((width, height), pygame.HWSURFACE | pygame.DOUBLEBUF)
    pygame.display.set_caption("CARLA Walker Manual Control")
    clock = pygame.time.Clock()

    # 카메라 이미지를 Pygame surface로 변환하여 저장할 변수
    # 전역 변수로 선언하거나, 클래스 내부 변수로 관리하면 콜백 밖에서도 접근 가능
    global current_image_surface 
    current_image_surface = None

    def process_image(image):
        global current_image_surface
        # CARLA Raw 이미지 데이터를 numpy 배열로 변환
        array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
        array = np.reshape(array, (image.height, image.width, 4)) # BGRA 포맷

        # BGRA -> RGB 변환 (채널 순서 변경)
        array = array[:, :, :3] # Alpha 채널 제거 (BGR)
        array = array[:, :, ::-1] # BGR -> RGB 순서로 뒤집기

        # NumPy 배열을 Pygame Surface로 변환
        current_image_surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))

    try:
        # 1. CARLA 월드에 접속
        client = carla.Client('localhost', 2000)
        client.set_timeout(5.0)
        world = client.get_world()

        # 2. 보행자 생성
        blueprint_library = world.get_blueprint_library()
        walker_bp = random.choice(blueprint_library.filter('walker.pedestrian.*'))
        spawn_point = random.choice(world.get_map().get_spawn_points())
        
        # 보행자가 지면에 제대로 서 있도록 Z 좌표 조정
        spawn_point.location.z += 1.0 # 보행자 블루프린트에 따라 조절 필요
        
        walker = world.spawn_actor(walker_bp, spawn_point)
        actor_list.append(walker)
        print(f"Spawned walker: {walker.id}")

        # 3. 카메라 센서 생성 및 보행자에 부착
        camera_bp = blueprint_library.find('sensor.camera.rgb')
        camera_bp.set_attribute('image_size_x', str(width))
        camera_bp.set_attribute('image_size_y', str(height))
        # 카메라 위치 조정: 보행자 눈 높이에 맞게 Z 조절
        # 수정된 3인칭 시점 코드
        camera_transform = carla.Transform(
            carla.Location(x=-2.0, z=2.5),  # 뒤로 2m, 위로 2.5m 이동
            carla.Rotation(pitch=-15.0)     # 아래로 15도 기울이기
        )
        camera = world.spawn_actor(camera_bp, camera_transform, attach_to=walker)
        actor_list.append(camera)

        # 4. 카메라 데이터 처리 설정
        camera.listen(process_image)

        # 5. 메인 루프 시작
        while True:
            # Pygame 이벤트 처리 (창 닫기 등)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return

            # 키보드 입력 감지 및 보행자 제어
            keys = pygame.key.get_pressed()
            control = carla.WalkerControl()
            
            # 현재 보행자의 transform을 가져와 회전 정보 추출
            current_transform = walker.get_transform()
            current_rotation = current_transform.rotation
            
            # 현재 yaw 값을 기반으로 새로운 yaw 값을 계산
            new_yaw = current_rotation.yaw
            rotation_speed = 1.5 # 회전 속도 (도/프레임, 조절 가능)

            if keys[pygame.K_a]: # 왼쪽으로 회전
                new_yaw -= rotation_speed
            if keys[pygame.K_d]: # 오른쪽으로 회전
                new_yaw += rotation_speed
            
            # Yaw 값을 -180 ~ 180도 범위로 유지 (선택 사항이지만 일관성 유지에 좋음)
            if new_yaw > 180: new_yaw -= 360
            if new_yaw < -180: new_yaw += 360

            # 새로운 Yaw 값으로 carla.Rotation 객체 생성
            # Pitch와 Roll은 변경하지 않고 현재 값을 유지
            new_rotation = carla.Rotation(pitch=current_rotation.pitch, 
                                          yaw=new_yaw, 
                                          roll=current_rotation.roll)
            
            # 이 새로운 Rotation에서 전방 벡터를 얻어와 control.direction에 할당
            control.direction = new_rotation.get_forward_vector()

            # 속도 제어
            if keys[pygame.K_w]:
                control.speed = 2.5 # 걷는 속도
            elif keys[pygame.K_s]:
                control.speed = 1.5 # 뒤로 걷는 속도
                control.direction *= -1.0 # 뒤로 걷기 위해 방향 벡터 반전
            else:
                control.speed = 0.0 # 키를 떼면 정지

            if keys[pygame.K_SPACE]:
                control.jump = True

            walker.apply_control(control)

            # Pygame 화면 업데이트 (카메라 이미지가 준비되었을 때만 그리기)
            if current_image_surface is not None:
                display.blit(current_image_surface, (0, 0))
            
            pygame.display.flip()
            clock.tick_busy_loop(60) # 초당 60프레임으로 제한

    finally:
        # 6. 종료 시 모든 액터 정리
        print('Destroying actors...')
        for actor in actor_list:
            if actor.is_alive: # 이미 파괴된 액터는 건너뛰기
                actor.destroy()
        pygame.quit()
        print('Cleaned up!')


if __name__ == '__main__':
    game_loop()