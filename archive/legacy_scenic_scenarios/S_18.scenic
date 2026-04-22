'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_18.scenic --2d --model scenic.simulators.carla.model --simulate
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
# ego = new Car following roadDirection from spot for Range(-40, -30)

# S_18
# 1. 자전거 무단 횡단
bicycle1 = new Bicycle at (-24.8, 55),
    with regionContainedIn None,
    with heading 0 deg,
    with behavior CrossingBehavior(ego, min_speed=BICYCLE_MIN_SPEED, threshold=THRESHOLD)

bicycle2 = new Bicycle at (-15, -9.9),
    with regionContainedIn None,
    with heading 180 deg,
    with behavior CrossingBehavior(ego, min_speed=BICYCLE_MIN_SPEED, threshold=THRESHOLD)

bicycle3 = new Bicycle at (-93, -8),
    with regionContainedIn None,
    with heading 180 deg,
    with behavior CrossingBehavior(ego, min_speed=BICYCLE_MIN_SPEED, threshold=THRESHOLD)
