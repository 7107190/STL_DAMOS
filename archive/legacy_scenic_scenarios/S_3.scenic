'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_3.scenic --2d --model scenic.simulators.carla.model --simulate
'''
param map = localPath('../Maps/Town10HD.xodr')
param carla_map = 'Town10HD'
model scenic.simulators.carla.model
lane = Uniform(*network.lanes)

BICYCLE_MIN_SPEED = 2
PED_MIN_SPEED = 1
THRESHOLD = 15
spot = new OrientedPoint on lane.centerline
ego = new Car following roadDirection from spot for Range(-40, -30),
    with behavior AutopilotBehavior(),
    with rolename "ego"
# -------------------------------------------------------------------------------

param weather = {
  'cloudiness': 80,
  'fog_density': 100,              # 최대 수준
  'fog_distance': 10,              # 10m 부터 뿌옇게
  'fog_falloff': 1.0,              # 서서히 퍼지는 타입
  'wetness': 10,
  'sun_altitude_angle': 15
}

# terminate after 30 seconds

