'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_2.scenic --2d --model scenic.simulators.carla.model --simulate
'''
param map = localPath('../Maps/Town10HD.xodr')
param carla_map = 'Town10HD'
model scenic.simulators.carla.model
# lane = Uniform(*network.lanes)

# BICYCLE_MIN_SPEED = 2
# PED_MIN_SPEED = 1
# THRESHOLD = 15
# spot = new OrientedPoint on lane.centerline
# ego = new Car following roadDirection from spot for Range(-40, -30),
#     with behavior AutopilotBehavior(),
#     with rolename "ego"
# -------------------------------------------------------------------------------

param weather = {
  'cloudiness': 95,                # 흐림
  'precipitation': 95,             # 매우 강한 비
  'precipitation_deposits': 80,    # 노면 물웅덩이/물자국
  'wind_intensity': 30,            # 비에 따른 바람
  'wetness': 90,                   # 노면 매우 젖음(반사, 글레어)
  'fog_density': 20,               # 가벼운 안개 섞임 가능
  'sun_altitude_angle': 10
}

# terminate after 30 seconds

