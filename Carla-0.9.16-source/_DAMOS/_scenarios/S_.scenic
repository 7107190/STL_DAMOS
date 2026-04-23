# --- 실행 예 ---
# ./CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
# scenic _DAMOS/_scenarios/S_.scenic --model scenic.simulators.carla.model --simulate --2d --scenario BaseSetup --param N_SCENARIOS 2

                        #    ╱|、
                        #   (˚ˎ。7
                        #   |、˜〵
                        #   じしˍ,)
# 전역 파라미터/모델
param map = localPath('../../Scenic/Maps/Town10HD.xodr')
param carla_map = 'Town10HD'
param snapToGroundDefault = True
model scenic.simulators.carla.model   # 2D 호환: 실행 시 --2d 옵션
param EGO_BLUEPRINT = 'vehicle.ford.mustang'
param EGO_COLOR = Color(1, 0, 0)

param weather = Uniform(
  {'cloudiness': 30, 'sun_azimuth_angle': 270, 'sun_altitude_angle': 5, 'scattering_intensity': 60, 'wetness': 20},
  {'cloudiness': 95, 'precipitation': 95, 'precipitation_deposits': 80, 'wind_intensity': 30, 'wetness': 90, 'fog_density': 20, 'sun_altitude_angle': 10},
  {'cloudiness': 80, 'fog_density': 100, 'fog_distance': 10, 'fog_falloff': 1.0, 'wetness': 10, 'sun_altitude_angle': 15},
  {'cloudiness': 60, 'sun_altitude_angle': -20, 'wetness': 95, 'fog_density': 10, 'scattering_intensity': 40},
)

# 초기 시나리오: ego 생성 + 분기 실행
scenario BaseSetup():
  setup:
    lane = Uniform(*network.lanes)
    spot = new OrientedPoint on lane.centerline
    EGO_BP = globalParameters.get('EGO_BLUEPRINT', 'vehicle.ford.mustang')
    EGO_CAR_COLOR = globalParameters.get('EGO_COLOR', Color(1, 0, 0))
    ego = new Car following roadDirection from spot for Range(-40, -30), \
        with blueprint EGO_BP, \
        with color EGO_CAR_COLOR, \
        with behavior AutopilotBehavior(), \
        with rolename "ego", \
        with snapToGround True

  compose:
    # 실행할 시나리오 개수 (CLI: --param N_SCENARIOS 5)
    N = globalParameters.get('N_SCENARIOS', 2)
    SELECTED = globalParameters.get('SELECTED_SCENARIO', 'random')
    for i in range(N):
      if SELECTED == 'S1':
        do S1()
      elif SELECTED == 'S2':
        do S2()
      elif SELECTED == 'S3':
        do S3()
      elif SELECTED == 'S4':
        do S4()
      elif SELECTED == 'S5':
        do S5()
      elif SELECTED == 'S6':
        do S6()
      elif SELECTED == 'S7':
        do S7()
      elif SELECTED == 'S8':
        do S8()
      elif SELECTED == 'S9':
        do S9()
      else:
        do Uniform(S1(), S2(), S3(), S4(), S5(), S6(), S7(), S8(), S9())
    do S_end()


scenario S_end():
  setup:
    terminate after 3000 seconds

scenario S1():  # 보행자 무단 횡단
  setup:
    PED_MIN_SPEED = 1
    THRESHOLD = 13
    N_PEDS = 3
    spots = [ new OrientedPoint on (Uniform(*network.sidewalks)).centerline
              for _ in range(N_PEDS) ]
    pedestrians = [
      new Pedestrian left of sp by 4,
          with heading sp.heading + 90 deg,
          with regionContainedIn None,
          with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
      for sp in spots
    ]
    print(f"S1: 무단횡단 {len(pedestrians)}")
    terminate after 1 seconds

scenario S2(): # 자전거 무단 횡단
  setup:
    BICYCLE_MIN_SPEED = 2
    THRESHOLD = 20
    N_BICYCLES = 3
    spots = [ new OrientedPoint on (Uniform(*network.sidewalks)).centerline
              for _ in range(N_BICYCLES) ]
    bicycles = [
      new Bicycle at sp.position,
          with regionContainedIn None,
          with heading sp.heading + 90 deg,
          with behavior CrossingBehavior(ego, min_speed=BICYCLE_MIN_SPEED, threshold=THRESHOLD)
      for sp in spots
    ]

    print(f"S2: 자전거 무단횡단 {len(bicycles)}대")
    terminate after 1 seconds

scenario S3(): # 비가시 영역 무단 횡단
  setup:
    PED_MIN_SPEED = 1
    THRESHOLD = 13
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
    print("S3: 정차 차량 사이 무단횡단 3명")
    terminate after 1 seconds

scenario S4():  # 도로위 장애물
  setup:
    N_OBSTACLES = 4
    spots = [ new OrientedPoint on (Uniform(*network.lanes)).centerline
              for _ in range(N_OBSTACLES) ]
    obstacles = [
      new Garbage at sp.position,
          with heading sp.heading
      for sp in spots
    ]
    print(f"S4: 도로위 장애물 {len(obstacles)}")
    terminate after 1 seconds

scenario S5(): # 인도 위 장애물
  setup:
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

    print("S5: 인도위 불법 주차")
    terminate after 1 seconds

scenario S6(): # 도로 공사로 인한 차선 감소
  setup:
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

    print("S6: 도로공사로 인한 차선 좁아짐")
    terminate after 1 seconds

scenario S7(): # 인도 공사로 인한 통행 불가
  setup:

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

    print("S7: 인도 공사로 통행불가")
    terminate after 1 seconds

scenario S8(): # 인도 내 군중
  setup:
    NUM_CLUSTERS = 3
    PEDS_PER_CLUSTER = 3
    CLUSTER_SPREAD = 3
    for k in range(NUM_CLUSTERS):
      SW = Uniform(*network.sidewalks)
      anchor = new OrientedPoint on SW.centerline
      # 군집당 보행자들 생성
      for j in range(PEDS_PER_CLUSTER):
        new Pedestrian at anchor offset by Range(-CLUSTER_SPREAD, CLUSTER_SPREAD) @ Range(-CLUSTER_SPREAD, CLUSTER_SPREAD),
          with heading anchor.heading - 90 deg,      # 보행 방향: 보도 법선 방향(예시)
          with regionContainedIn None                # 필요 시 None -> SW 로 바꿔 인도 내부 제한 가능
          # with regionContainedIn SW
    print(f"S8: 인도내 군중 {NUM_CLUSTERS * PEDS_PER_CLUSTER}명")
    terminate after 1 seconds

scenario S9(): # 인도 쓰레기 더미
  setup:
    NUM_CLUSTERS = 5
    OFFSETS = [1.0, 1.3]                  # 좌우로 깔 오프셋(m) 리스트 (예: 1m, 1m+0.3m)
    MODEL_LIST = ["static.prop.trashbag"] # 필요하면 여러 모델을 넣어 랜덤 사용
    for k in range(NUM_CLUSTERS):
      SW = Uniform(*network.sidewalks)
      obs = new OrientedPoint on SW.centerline

      # 좌/우 대칭 배치
      for d in OFFSETS:
        new Trash left  of obs by d,
          with model Uniform(*MODEL_LIST),
          with heading 0 deg,
          with regionContainedIn None
        new Trash right of obs by d,
          with model Uniform(*MODEL_LIST),
          with heading 0 deg,
          with regionContainedIn None

    print(f"S9: 인도 쓰레기 더미 {NUM_CLUSTERS * len(OFFSETS) * 2}개")
    terminate after 1 seconds
