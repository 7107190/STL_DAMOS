'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_8.scenic --2d --model scenic.simulators.carla.model --simulate
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

# S_8
# 6. 인도 공사로 인한 인도 통제
Barrier_a = new Prop at (56.8, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_b = new Prop at (55.5, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None
    
Barrier_c = new Prop at (54.2, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_d = new Prop at (52.9, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_e = new Prop at (51.6, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_f = new Prop at (50.3, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_g = new Prop at (49.0, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_h = new Prop at (47.7, -51.0),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_A = new Prop at (68.4, -55.6),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_B = new Prop at (68.4, -56.9),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_C = new Prop at (68.4, -58.2),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_D = new Prop at (68.4, -59.5),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_E = new Prop at (68.4, -60.8),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_F = new Prop at (68.4, -62.1),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_G = new Prop at (68.4, -63.4),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_101 = new Prop at (67.6, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_102 = new Prop at (66.3, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_103 = new Prop at (65.0, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_104 = new Prop at (63.7, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_105 = new Prop at (62.4, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_106 = new Prop at (61.1, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_107 = new Prop at (59.8, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_108 = new Prop at (58.5, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_109 = new Prop at (57.2, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_110 = new Prop at (55.9, -63.8),
    with blueprint "static.prop.streetbarrier",
    with heading 90 deg,
    with regionContainedIn None

Barrier_111 = new Prop at (54.6, -63.6),
    with blueprint "static.prop.streetbarrier",
    with heading 76.2 deg,
    with regionContainedIn None

Barrier_111 = new Prop at (53.4, -63.2),
    with blueprint "static.prop.streetbarrier",
    with heading 67.4 deg,
    with regionContainedIn None

Barrier_112 = new Prop at (52.3, -62.6),
    with blueprint "static.prop.streetbarrier",
    with heading 57.6 deg,
    with regionContainedIn None

Barrier_113 = new Prop at (51.3, -61.9),
    with blueprint "static.prop.streetbarrier",
    with heading 54.1 deg,
    with regionContainedIn None

Barrier_114 = new Prop at (50.3, -61.1),
    with blueprint "static.prop.streetbarrier",
    with heading 49 deg,
    with regionContainedIn None

Barrier_115 = new Prop at (49.4, -60.2),
    with blueprint "static.prop.streetbarrier",
    with heading 41 deg,
    with regionContainedIn None

Barrier_116 = new Prop at (48.6, -59.19),
    with blueprint "static.prop.streetbarrier",
    with heading 33 deg,
    with regionContainedIn None

Barrier_117 = new Prop at (48, -58),
    with blueprint "static.prop.streetbarrier",
    with heading 22 deg,
    with regionContainedIn None

Barrier_118 = new Prop at (47.5, -56.8),
    with blueprint "static.prop.streetbarrier",
    with heading 18 deg,
    with regionContainedIn None

Barrier_119 = new Prop at (47.2, -55.6),
    with blueprint "static.prop.streetbarrier",
    with heading 10 deg,
    with regionContainedIn None

Barrier_120 = new Prop at (47.0, -54.3),
    with blueprint "static.prop.streetbarrier",
    with heading 5 deg,
    with regionContainedIn None

Barrier_121 = new Prop at (47.0, -53.0),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_122 = new Prop at (47.0, -51.7),
    with blueprint "static.prop.streetbarrier",
    with heading 0 deg,
    with regionContainedIn None

Barrier_123 = new Prop at (53, -52.6),
    with blueprint "static.prop.trafficwarning",
    with heading 270 deg,
    with regionContainedIn None

Barrier_124 = new Prop at (67, -57),
    with blueprint "static.prop.trafficwarning",
    with heading 180 deg,
    with regionContainedIn None
