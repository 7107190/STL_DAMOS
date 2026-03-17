# 14. 인도 장애물 (통행은 가능하나 좁음)
'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
scenic Scenic/_scenarios/S_14.scenic --2d --model scenic.simulators.carla.model --simulate
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

# ---- 인도 임의 지점에 Trash 3개(가운데/좌/우) 배치 ----
SW = Uniform(*network.sidewalks)
obs = new OrientedPoint on SW.centerline


trash_center = new Trash at obs, 
    with model "static.prop.trashbag",
    with heading 0 deg, 
    with regionContainedIn None
trash_left   = new Trash left of obs by 1, 
    with model "static.prop.trashbag",
    with heading 0 deg,
    with regionContainedIn None
trash_left_left   = new Trash left of trash_left by 0.3, 
    with model "static.prop.trashbag",
    with heading 0 deg,
    with regionContainedIn None
trash_right  = new Trash right of obs by 1,
    with model "static.prop.trashbag",
    with heading 0 deg, 
    with regionContainedIn None
trash_right_right  = new Trash right of trash_right by 0.3,
    with model "static.prop.trashbag",
    with heading 0 deg, 
    with regionContainedIn None
