'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_20.scenic --2d --model scenic.simulators.carla.model --simulate
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
  'cloudiness': 60,
  'sun_altitude_angle': -20,       # 태양 아래(야간)
  'wetness': 95,
  'fog_density': 10,
  'scattering_intensity': 40
}

# terminate after 30 seconds

