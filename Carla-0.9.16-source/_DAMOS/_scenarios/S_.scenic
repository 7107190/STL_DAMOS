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
      INSTANCE_INDEX = i + 1
      if SELECTED == 'S1':
        do S1(INSTANCE_INDEX)
      elif SELECTED == 'S2':
        do S2(INSTANCE_INDEX)
      elif SELECTED == 'S3':
        do S3(INSTANCE_INDEX)
      elif SELECTED == 'S4':
        do S4(INSTANCE_INDEX)
      elif SELECTED == 'S5':
        do S5(INSTANCE_INDEX)
      elif SELECTED == 'S6':
        do S6(INSTANCE_INDEX)
      elif SELECTED == 'S7':
        do S7(INSTANCE_INDEX)
      elif SELECTED == 'S8':
        do S8(INSTANCE_INDEX)
      elif SELECTED == 'S9':
        do S9(INSTANCE_INDEX)
      else:
        do Uniform(
          S1(INSTANCE_INDEX), S2(INSTANCE_INDEX), S3(INSTANCE_INDEX),
          S4(INSTANCE_INDEX), S5(INSTANCE_INDEX), S6(INSTANCE_INDEX),
          S7(INSTANCE_INDEX), S8(INSTANCE_INDEX), S9(INSTANCE_INDEX)
        )
    do S_end()


scenario S_end():
  setup:
    terminate after 3000 seconds

scenario S1(instance_index=1):  # 보행자 무단 횡단
  setup:
    SCENARIO_ROLE = f"damos.S1.{instance_index}"
    PED_MIN_SPEED = 1
    THRESHOLD = 13
    N_PEDS = 3
    spots = [ new OrientedPoint on (Uniform(*network.sidewalks)).centerline
              for _ in range(N_PEDS) ]
    pedestrians = [
      new Pedestrian left of sp by 4,
          with heading sp.heading + 90 deg,
          with regionContainedIn None,
          with rolename SCENARIO_ROLE,
          with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
      for sp in spots
    ]
    print(f"S1#{instance_index}: 무단횡단 {len(pedestrians)}")
    terminate after 1 seconds

scenario S2(instance_index=1): # 자전거 무단 횡단
  setup:
    SCENARIO_ROLE = f"damos.S2.{instance_index}"
    BICYCLE_MIN_SPEED = 2
    THRESHOLD = 20
    N_BICYCLES = 3
    spots = [ new OrientedPoint on (Uniform(*network.sidewalks)).centerline
              for _ in range(N_BICYCLES) ]
    bicycles = [
      new Bicycle at sp.position,
          with regionContainedIn None,
          with heading sp.heading + 90 deg,
          with rolename SCENARIO_ROLE,
          with behavior CrossingBehavior(ego, min_speed=BICYCLE_MIN_SPEED, threshold=THRESHOLD)
      for sp in spots
    ]

    print(f"S2#{instance_index}: 자전거 무단횡단 {len(bicycles)}대")
    terminate after 1 seconds

scenario S3(instance_index=1): # 비가시 영역 무단 횡단
  setup:
    SCENARIO_ROLE = f"damos.S3.{instance_index}"
    PED_MIN_SPEED = 1
    THRESHOLD = 13
    ped_1 = new Pedestrian at (-14.5,-127.5),
        with regionContainedIn None,
        with heading 180 deg,
        with rolename SCENARIO_ROLE,
        with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
    ped_2 = new Pedestrian at (14,-144.0),
        with regionContainedIn None,
        with heading 0 deg,
        with rolename SCENARIO_ROLE,
        with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
    ped_3 = new Pedestrian at (107.5,-115.0),
        with regionContainedIn None,
        with heading 60 deg,
        with rolename SCENARIO_ROLE,
        with behavior CrossingBehavior(ego, min_speed=PED_MIN_SPEED, threshold=THRESHOLD)
    print(f"S3#{instance_index}: 정차 차량 사이 무단횡단 3명")
    terminate after 1 seconds

scenario S4(instance_index=1):  # 도로위 장애물
  setup:
    SCENARIO_ROLE = f"damos.S4.{instance_index}"
    N_OBSTACLES = 4
    spots = [ new OrientedPoint on (Uniform(*network.lanes)).centerline
              for _ in range(N_OBSTACLES) ]
    obstacles = [
      new Car at sp.position,
          with blueprint Uniform(
            "vehicle.jeep.wrangler_rubicon",
            "vehicle.nissan.patrol",
            "vehicle.tesla.model3",
            "vehicle.toyota.prius"
          ),
          with heading sp.heading,
          with rolename SCENARIO_ROLE,
          with regionContainedIn None
      for sp in spots
    ]
    print(f"S4#{instance_index}: 도로위 정차 차량 장애물 {len(obstacles)}")
    terminate after 3000 seconds

scenario S5(instance_index=1): # 인도 위 장애물
  setup:
    SCENARIO_ROLE = f"damos.S5.{instance_index}"
    CAR_1 = new Car at (-59, 20),
        with blueprint "vehicle.tesla.model3",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    CAR_2 = new Car at (-59, 17),
        with blueprint "vehicle.jeep.wrangler_rubicon",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    CAR_3 = new Car at (-59, 14),
        with blueprint "vehicle.ford.mustang",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    CAR_4 = new Car at (-59, 7.8),
        with blueprint "vehicle.toyota.prius",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    CAR_5 = new Car at (-59, 5),
        with blueprint "vehicle.nissan.patrol",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    CAR_6 = new Car at (-59, 1),
        with blueprint "vehicle.nissan.micra",
        with heading 90 deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    print(f"S5#{instance_index}: 인도위 불법 주차")
    terminate after 1 seconds

scenario S6(instance_index=1): # 도로 공사로 인한 차선 감소
  setup:
    SCENARIO_ROLE = f"damos.S6.{instance_index}"
    BARRIER_SPECS = [
      ((-82.4, -137.9), "static.prop.streetbarrier", 64),
      ((-83.4, -137.0), "static.prop.streetbarrier", 34),
      ((-84.2, -135.8), "static.prop.streetbarrier", 32),
      ((-85.0, -134.6), "static.prop.streetbarrier", 32),
      ((-85.8, -133.4), "static.prop.streetbarrier", 32),
      ((-86.6, -132.2), "static.prop.streetbarrier", 33),
      ((-87.7, -131.1), "static.prop.streetbarrier", 56),
      ((-88.9, -130.3), "static.prop.streetbarrier", 56),
      ((-90.2, -129.4), "static.prop.streetbarrier", 53),
      ((-91.4, -128.4), "static.prop.streetbarrier", 49),
      ((-92.5, -127.4), "static.prop.streetbarrier", 47),
      ((-93.6, -126.4), "static.prop.streetbarrier", 46),
      ((-94.7, -125.3), "static.prop.streetbarrier", 44),
      ((-95.8, -124.1), "static.prop.streetbarrier", 42),
      ((-96.9, -122.9), "static.prop.streetbarrier", 41),
      ((-98.0, -121.7), "static.prop.streetbarrier", 39),
      ((-99.0, -120.4), "static.prop.streetbarrier", 37),
      ((-99.9, -119.1), "static.prop.streetbarrier", 34),
      ((-100.8, -117.8), "static.prop.streetbarrier", 33),
      ((-101.6, -116.5), "static.prop.streetbarrier", 31),
      ((-102.4, -115.2), "static.prop.streetbarrier", 30),
      ((-103.2, -113.9), "static.prop.streetbarrier", 29),
      ((-103.9, -112.6), "static.prop.streetbarrier", 28),
      ((-104.6, -111.3), "static.prop.streetbarrier", 27),
      ((-105.2, -110.0), "static.prop.streetbarrier", 24),
      ((-105.8, -108.7), "static.prop.streetbarrier", 25),
      ((-106.4, -107.4), "static.prop.streetbarrier", 25),
      ((-107.0, -106.1), "static.prop.streetbarrier", 25),
      ((-107.7, -104.9), "static.prop.streetbarrier", 35),
      ((-108.6, -103.7), "static.prop.streetbarrier", 37),
      ((-109.5, -102.6), "static.prop.streetbarrier", 40),
      ((-110.4, -101.6), "static.prop.streetbarrier", 41),
      ((-111.3, -100.6), "static.prop.streetbarrier", 42),
      ((-112.2, -99.6), "static.prop.streetbarrier", 42),
      ((-113.1, -98.6), "static.prop.streetbarrier", 42),
      ((-111.0, -103.0), "static.prop.trafficwarning", 270),
    ]
    for pos, blueprint_id, heading_deg in BARRIER_SPECS:
      new Prop at pos,
        with blueprint blueprint_id,
        with heading heading_deg deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    print(f"S6#{instance_index}: 도로공사로 인한 차선 좁아짐")
    terminate after 1 seconds

scenario S7(instance_index=1): # 인도 공사로 인한 통행 불가
  setup:
    SCENARIO_ROLE = f"damos.S7.{instance_index}"
    BARRIER_SPECS = [
      ((56.8, -51.0), "static.prop.streetbarrier", 90),
      ((55.5, -51.0), "static.prop.streetbarrier", 90),
      ((54.2, -51.0), "static.prop.streetbarrier", 90),
      ((52.9, -51.0), "static.prop.streetbarrier", 90),
      ((51.6, -51.0), "static.prop.streetbarrier", 90),
      ((50.3, -51.0), "static.prop.streetbarrier", 90),
      ((49.0, -51.0), "static.prop.streetbarrier", 90),
      ((47.7, -51.0), "static.prop.streetbarrier", 90),
      ((68.4, -55.6), "static.prop.streetbarrier", 0),
      ((68.4, -56.9), "static.prop.streetbarrier", 0),
      ((68.4, -58.2), "static.prop.streetbarrier", 0),
      ((68.4, -59.5), "static.prop.streetbarrier", 0),
      ((68.4, -60.8), "static.prop.streetbarrier", 0),
      ((68.4, -62.1), "static.prop.streetbarrier", 0),
      ((68.4, -63.4), "static.prop.streetbarrier", 0),
      ((67.6, -63.8), "static.prop.streetbarrier", 90),
      ((66.3, -63.8), "static.prop.streetbarrier", 90),
      ((65.0, -63.8), "static.prop.streetbarrier", 90),
      ((63.7, -63.8), "static.prop.streetbarrier", 90),
      ((62.4, -63.8), "static.prop.streetbarrier", 90),
      ((61.1, -63.8), "static.prop.streetbarrier", 90),
      ((59.8, -63.8), "static.prop.streetbarrier", 90),
      ((58.5, -63.8), "static.prop.streetbarrier", 90),
      ((57.2, -63.8), "static.prop.streetbarrier", 90),
      ((55.9, -63.8), "static.prop.streetbarrier", 90),
      ((54.6, -63.6), "static.prop.streetbarrier", 76.2),
      ((53.4, -63.2), "static.prop.streetbarrier", 67.4),
      ((52.3, -62.6), "static.prop.streetbarrier", 57.6),
      ((51.3, -61.9), "static.prop.streetbarrier", 54.1),
      ((50.3, -61.1), "static.prop.streetbarrier", 49),
      ((49.4, -60.2), "static.prop.streetbarrier", 41),
      ((48.6, -59.19), "static.prop.streetbarrier", 33),
      ((48.0, -58.0), "static.prop.streetbarrier", 22),
      ((47.5, -56.8), "static.prop.streetbarrier", 18),
      ((47.2, -55.6), "static.prop.streetbarrier", 10),
      ((47.0, -54.3), "static.prop.streetbarrier", 5),
      ((47.0, -53.0), "static.prop.streetbarrier", 0),
      ((47.0, -51.7), "static.prop.streetbarrier", 0),
      ((53.0, -52.6), "static.prop.trafficwarning", 270),
      ((67.0, -57.0), "static.prop.trafficwarning", 180),
    ]
    for pos, blueprint_id, heading_deg in BARRIER_SPECS:
      new Prop at pos,
        with blueprint blueprint_id,
        with heading heading_deg deg,
        with rolename SCENARIO_ROLE,
        with regionContainedIn None

    print(f"S7#{instance_index}: 인도 공사로 통행불가")
    terminate after 1 seconds

scenario S8(instance_index=1): # 인도 내 군중
  setup:
    SCENARIO_ROLE = f"damos.S8.{instance_index}"
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
          with rolename SCENARIO_ROLE,
          with regionContainedIn None                # 필요 시 None -> SW 로 바꿔 인도 내부 제한 가능
          # with regionContainedIn SW
    print(f"S8#{instance_index}: 인도내 군중 {NUM_CLUSTERS * PEDS_PER_CLUSTER}명")
    terminate after 1 seconds

scenario S9(instance_index=1): # 인도 쓰레기 더미
  setup:
    SCENARIO_ROLE = f"damos.S9.{instance_index}"
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
          with rolename SCENARIO_ROLE,
          with regionContainedIn None
        new Trash right of obs by d,
          with model Uniform(*MODEL_LIST),
          with heading 0 deg,
          with rolename SCENARIO_ROLE,
          with regionContainedIn None

    print(f"S9#{instance_index}: 인도 쓰레기 더미 {NUM_CLUSTERS * len(OFFSETS) * 2}개")
    terminate after 1 seconds
