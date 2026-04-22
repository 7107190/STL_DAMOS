'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_9.scenic --2d --model scenic.simulators.carla.model --simulate
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

# S_9
# 8. 인도 불법주차로 인한 길 좁아짐
CAR_1 = new Car at (-59, 20),
    with blueprint "vehicle.tesla.model3",
    with heading 90 deg,
    with regionContainedIn None

CAR_2 = new Car at (-59, 17),
    with blueprint "vehicle.jeep.wrangler_rubicon",
    with heading 90 deg,
    with regionContainedIn None

CAR_3 = new Car at (-59, 14),
    with blueprint "vehicle.ford.mustang",
    with heading 90 deg,
    with regionContainedIn None

CAR_4 = new Car at (-59, 7.8),
    with blueprint "vehicle.toyota.prius",
    with heading 90 deg,
    with regionContainedIn None

CAR_5 = new Car at (-59, 5),
    with blueprint "vehicle.nissan.patrol",
    with heading 90 deg,
    with regionContainedIn None

CAR_6 = new Car at (-59, 1),
    with blueprint "vehicle.nissan.micra",
    with heading 90 deg,
    with regionContainedIn None
