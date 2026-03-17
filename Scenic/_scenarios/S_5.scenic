'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_5.scenic --2d --model scenic.simulators.carla.model --simulate
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

# S_5
# 2. 보행자 무단횡단
pedestrian1 = new Pedestrian at (114.5,-35),
    with heading 90 deg,
    with regionContainedIn None,
    with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)

pedestrian2 = new Pedestrian at (-57,-46),
    with heading 270 deg,
    with regionContainedIn None,
    with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)      # ego가 12m이내에 접근하면 1m/s로 횡단

