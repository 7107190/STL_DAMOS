# DAMOS Project: M2X Decentralized Cooperative Autonomous Driving Simulation

## 1. Project Overview
* **총괄 책임자 (사용자):** 김기웅 (CARLA 메인 시뮬레이터 및 전체 통합 스크립트 개발 담당)
* **목표:** 2026년 10월 공인 인증 평가(KOLAS) 성공적 수검
* **핵심 평가 지표:**
  1. **18초 이내 연동 처리 속도:** CARLA 센서 데이터 송신부터 서브 컴퓨터의 경로 최적화 수신 후 Tick 전진까지 18초 이내 달성.
  2. **5초 이내 점유 영역 예측:** 타 객체의 미래 5초간 이동 궤적(Trajectory) 예측 및 공유.

## 2. System Architecture (Hybrid 구조)
* **메인 컴퓨터 (CARLA & Scenic):** * 동기화 모드(Synchronous Mode)로 구동.
  * Scenic을 활용하여 100개의 엣지 케이스(비가시권 사각지대, 무단횡단 등) 자동 주입 및 리셋.
* **서브 컴퓨터 (자율주행차, 배달 로봇 등):**
  * **외부 통신 (ROS2 DDS):** PC 간 데이터 교환은 ROS2 표준 메시지 사용 (네트워크 병목 최소화).
  * **내부 연산 (Pure Python/PyTorch):** 서브 컴퓨터 내부의 AI 모듈(인지/예측) 간 데이터 전달은 ROS2를 거치지 않고 파이썬 메모리(Tensor) 상에서 직접 처리하여 속도 극대화.

## 3. M2X 통신 데이터 규격 (ROS2 Payload)
서브 컴퓨터 간 공유해야 할 3가지 핵심 데이터 규격 (영지식팀 서브 1 시스템 통과):

1. **이동 정보 (Kinematic State):** `(x, y, z, yaw, v, a)`
   * *보안:* 영지식 증명(ZKP) 필수 적용
2. **환경 센서 데이터 (Perception):**
   * Bounding Box `[x, y, w, h, class]`
   * 이상 상황 플래그 (Event ID)
   * 비가시권 복원용 압축 특징맵 (Feature Map / Latent Vector)
   * *보안:* 특징맵 등 고용량 텐서는 ZKP 제외 (속도 방어), 이상 상황 플래그는 ZKP 적용.
3. **경로 최적화 정보 (Ego Trajectory):** 자차가 앞으로 이동할 미래 5초간의 Waypoint 배열.
   * *보안:* 영지식 증명(ZKP) 필수 적용

## 4. Sub-Agent Core Logic (비동기 t-1 파이프라인)
서브 컴퓨터는 타 에이전트의 연산을 기다리지 않고, **이전 틱(t-1)** 데이터를 활용하여 지연 없는 5단계 병렬 처리를 수행함.

* **Step 1 (수신):** 메인 컴퓨터로부터 현재 시점(t)의 센서 데이터 수신.
* **Step 2 (수신):** 타 에이전트들이 네트워크에 뿌려둔 직전 시점(t-1)의 공유 데이터 즉시 로드.
* **Step 3 (비가시권 인지):** 내 센서 데이터와 타인의 공유 데이터를 융합하여 사각지대 객체 인지 (이예린/이성규 모듈).
* **Step 4 (점유 영역 예측):** 인지된 타 객체의 향후 5초 궤적(Trajectory) 예측 (박준범 모듈).
* **Step 5 (경로 계획 - Rule-based):** * 무거운 AI Planning 생략. 단순 기하학적 교차 검증(Intersection Check) 사용.
  * `IF` 타 객체의 예측 궤적과 내 기본 경로가 겹치면 -> **긴급 제동 (정지 Waypoint 생성 및 공유)**
  * `ELSE` 겹치지 않으면 -> **직진 (CARLA 기본 Waypoint 유지 및 공유)**

## 5. Development Milestones (Target: 2026.10)
* **Phase 1 (~4월 말):** CARLA & ROS2 동기화 모드 메인 루프 뼈대 구축.
* **Phase 2 (~5월 말):** Scenic 100개 엣지 케이스 자동 주입/리셋 환경 세팅.
* **Phase 3 (~7월 말):** 서브 컴퓨터 AI 파이프라인(Step 1~4) 결합 및 Rule-based 제어(Step 5) 완성.
* **Phase 4 (~8월 말):** ZK 통신망 통합 및 '18초 지표' 달성을 위한 코드 프로파일링/최적화.
* **Phase 5 (~9월 말):** 평가용 100-Scenario 무한 리허설 및 `Integration_Summary.json` 로깅 자동화.

## 6. AI Assistant (Claude) Coding Guidelines
* 코드를 작성할 때 복잡한 Planning 알고리즘(MPC, RL 등)을 제안하지 말 것. 무조건 기하학적 충돌 기반의 **Rule-based (If-Then) 제어**로 간소화하여 성능(18초)을 방어할 것.
* CARLA 메인 스크립트를 짤 때는 항상 **시간 측정(Timer) 로직**을 포함하여 1 Tick 처리가 18초 이내에 완료되는지 콘솔에 로깅할 것.
* 서브 컴퓨터 내부 로직 코드를 생성할 때는 ROS2 Node 선언을 최소화(입출력 단 1개)하고, 내부는 순수 Python 함수 호출로 구성할 것.