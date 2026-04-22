'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_1.scenic --2d --model scenic.simulators.carla.model --simulate
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
  'cloudiness': 30,
  'sun_azimuth_angle': 270,        # 서쪽(예: 270도) — 도로에서 역광 유발
  'sun_altitude_angle': 5,         # 낮은 태양 (일몰/일출)
  'scattering_intensity': 60,      # 플레어/색번짐 강조
  'wetness': 20
}

# terminate after 30 seconds