# 11. 인도 내의 군중으로 인한 시야확보 불가
'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_11.scenic --2d --model scenic.simulators.carla.model --simulate
'''
param map = localPath('../Maps/Town10HD.xodr')
param carla_map = 'Town10HD'
model scenic.simulators.carla.model
lane = Uniform(*network.lanes)
spot = new OrientedPoint on lane.centerline
# ego = new Car following roadDirection from spot for Range(-40, -30),
#     with behavior AutopilotBehavior(),
#     with rolename "ego"
# ego = new Car following roadDirection from spot for Range(-40, -30)

# ---- 인도 임의 지점에 시민들 배치 ----
SW = Uniform(*network.sidewalks)
obs = new OrientedPoint on SW.centerline

for i in range(15):
    new Pedestrian at obs offset by Range(-3, 3) @ Range(-3, 3),  
        with heading 0 deg, 
        with regionContainedIn None

