'''실행 터미널 명령어
./CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic _DAMOS/_scenarios/S_13.scenic --2d --model scenic.simulators.carla.model --simulate
'''
param map = localPath('../../Scenic/Maps/Town10HD.xodr')
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

# S_13
# 3. 차량 두대 사이에 사람 갑툭튀
ped_1 = new Pedestrian at (-14.5,-127.5),
    with regionContainedIn None,
    with heading 180 deg,
    with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
ped_2 = new Pedestrian at (14,-144.0),
    with regionContainedIn None,
    with heading 0 deg,
    with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
ped_3 = new Pedestrian at (107.5,-115.0),
    with regionContainedIn None,
    with heading 60 deg,
    with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
