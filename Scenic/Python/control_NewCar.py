import carla

client = carla.Client('localhost', 2000)
world = client.get_world()
blueprint_library = world.get_blueprint_library()

bp = blueprint_library.find('vehicle.tesla.model3')
spawn_point = world.get_map().get_spawn_points()[0]
ego_vehicle = world.spawn_actor(bp, spawn_point)

# 제어 예시
control = carla.VehicleControl(throttle=0.5, steer=10.0)
ego_vehicle.apply_control(control)