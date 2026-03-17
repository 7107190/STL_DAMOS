import carla

client = carla.Client('localhost', 2000)
world = client.get_world()
blueprint_library = world.get_blueprint_library()

vehicles = world.get_actors().filter('vehicle.*')
ego_vehicle = vehicles[0]

# 제어 예시
control = carla.VehicleControl(throttle=0.05, steer=0.0)
ego_vehicle.apply_control(control)