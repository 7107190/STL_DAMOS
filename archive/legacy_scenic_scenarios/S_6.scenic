# 길가에 장애물이?
'''실행 터미널 명령어
../CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town01
scenic Scenic/_scenarios/S_6.scenic -S --2d --count 1
'''
                        #    ╱|、
                        #   (˚ˎ。7  
                        #   |、˜〵          
                        #   じしˍ,)

param use2DMap = True
param map = localPath('../Maps/Town01.xodr')  # CARLA Town 맵
param carla_map = 'Town01'
model scenic.simulators.carla.model

# make sure to put '*' to uniformly randomly select from all elements of the list, 'lanes'
lane = Uniform(*network.lanes)

ego = new Car following roadDirection from spot for Range(-40, -30),
    with behavior AutopilotBehavior(),
    with rolename "ego"
# ego = new Car following roadDirection from spot for Range(-40, -30)

obstacle = new Garbage following roadDirection from ego for Range(20, 30)


        

require (distance to intersection) > 50
terminate when (distance to obstacle) > 50