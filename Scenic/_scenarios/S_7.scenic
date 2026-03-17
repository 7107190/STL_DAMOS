'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_7.scenic --2d --model scenic.simulators.carla.model --simulate
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

# S_7
# 5. 도로 공사로 인해 폭 좁아짐
Barrier_1 = new Prop at (-82.4, -137.9),
    with blueprint "static.prop.streetbarrier",
    with heading 64 deg,
    with regionContainedIn None

Barrier_2 = new Prop at (-83.4, -137.0),
    with blueprint "static.prop.streetbarrier",
    with heading 34 deg,
    with regionContainedIn None

Barrier_3 = new Prop at (-84.2, -135.8),
    with blueprint "static.prop.streetbarrier",
    with heading 32 deg,
    with regionContainedIn None

Barrier_4 = new Prop at (-85.0, -134.6),
    with blueprint "static.prop.streetbarrier",
    with heading 32 deg,
    with regionContainedIn None

Barrier_5 = new Prop at (-85.8, -133.4),
    with blueprint "static.prop.streetbarrier",
    with heading 32 deg,
    with regionContainedIn None

Barrier_6 = new Prop at (-86.6, -132.2),
    with blueprint "static.prop.streetbarrier",
    with heading 33 deg,
    with regionContainedIn None

Barrier_6 = new Prop at (-87.7, -131.1),
    with blueprint "static.prop.streetbarrier",
    with heading 56 deg,
    with regionContainedIn None

Barrier_7 = new Prop at (-88.9, -130.3),
    with blueprint "static.prop.streetbarrier",
    with heading 56 deg,
    with regionContainedIn None

Barrier_8 = new Prop at (-90.2, -129.4),
    with blueprint "static.prop.streetbarrier",
    with heading 53 deg,
    with regionContainedIn None

Barrier_9 = new Prop at (-91.4, -128.4),
    with blueprint "static.prop.streetbarrier",
    with heading 49 deg,
    with regionContainedIn None

Barrier_10 = new Prop at (-92.5, -127.4),
    with blueprint "static.prop.streetbarrier",
    with heading 47 deg,
    with regionContainedIn None

Barrier_11 = new Prop at (-93.6, -126.4),
    with blueprint "static.prop.streetbarrier",
    with heading 46 deg,
    with regionContainedIn None

Barrier_12 = new Prop at (-94.7, -125.3),
    with blueprint "static.prop.streetbarrier",
    with heading 44 deg,
    with regionContainedIn None

Barrier_13 = new Prop at (-95.8, -124.1),
    with blueprint "static.prop.streetbarrier",
    with heading 42 deg,
    with regionContainedIn None

Barrier_14 = new Prop at (-96.9, -122.9),
    with blueprint "static.prop.streetbarrier",
    with heading 41 deg,
    with regionContainedIn None

Barrier_15 = new Prop at (-98.0, -121.7),
    with blueprint "static.prop.streetbarrier",
    with heading 39 deg,
    with regionContainedIn None

Barrier_16 = new Prop at (-99.0, -120.4),
    with blueprint "static.prop.streetbarrier",
    with heading 37 deg,
    with regionContainedIn None

Barrier_17 = new Prop at (-99.9, -119.1),
    with blueprint "static.prop.streetbarrier",
    with heading 34 deg,
    with regionContainedIn None

Barrier_18 = new Prop at (-100.8, -117.8),
    with blueprint "static.prop.streetbarrier",
    with heading 33 deg,
    with regionContainedIn None

Barrier_19 = new Prop at (-101.6, -116.5),
    with blueprint "static.prop.streetbarrier",
    with heading 31 deg,
    with regionContainedIn None

Barrier_20 = new Prop at (-102.4, -115.2),
    with blueprint "static.prop.streetbarrier",
    with heading 30 deg,
    with regionContainedIn None

Barrier_21 = new Prop at (-103.2, -113.9),
    with blueprint "static.prop.streetbarrier",
    with heading 29 deg,
    with regionContainedIn None

Barrier_22 = new Prop at (-103.9, -112.6),
    with blueprint "static.prop.streetbarrier",
    with heading 28 deg,
    with regionContainedIn None

Barrier_23 = new Prop at (-104.6, -111.3),
    with blueprint "static.prop.streetbarrier",
    with heading 27 deg,
    with regionContainedIn None

Barrier_24 = new Prop at (-105.2, -110),
    with blueprint "static.prop.streetbarrier",
    with heading 24 deg,
    with regionContainedIn None

Barrier_25 = new Prop at (-105.8, -108.7),
    with blueprint "static.prop.streetbarrier",
    with heading 25 deg,
    with regionContainedIn None

Barrier_26 = new Prop at (-106.4, -107.4),
    with blueprint "static.prop.streetbarrier",
    with heading 25 deg,
    with regionContainedIn None

Barrier_27 = new Prop at (-107, -106.1),
    with blueprint "static.prop.streetbarrier",
    with heading 25 deg,
    with regionContainedIn None

Barrier_28 = new Prop at (-107.7, -104.9),
    with blueprint "static.prop.streetbarrier",
    with heading 35 deg,
    with regionContainedIn None

Barrier_29 = new Prop at (-108.6, -103.7),
    with blueprint "static.prop.streetbarrier",
    with heading 37 deg,
    with regionContainedIn None

Barrier_30 = new Prop at (-109.5, -102.6),
    with blueprint "static.prop.streetbarrier",
    with heading 40 deg,
    with regionContainedIn None

Barrier_31 = new Prop at (-110.4, -101.6),
    with blueprint "static.prop.streetbarrier",
    with heading 41 deg,
    with regionContainedIn None

Barrier_32 = new Prop at (-111.3, -100.6),
    with blueprint "static.prop.streetbarrier",
    with heading 42 deg,
    with regionContainedIn None

Barrier_33 = new Prop at (-112.2, -99.6),
    with blueprint "static.prop.streetbarrier",
    with heading 42 deg,
    with regionContainedIn None

Barrier_34 = new Prop at (-113.1, -98.6),
    with blueprint "static.prop.streetbarrier",
    with heading 42 deg,
    with regionContainedIn None

Barrier_35 = new Prop at (-111, -103),
    with blueprint "static.prop.trafficwarning",
    with heading 270 deg,
    with regionContainedIn None
