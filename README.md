# 🚗 DAMOS: M2X 탈중앙형 협력 자율주행 통합 시뮬레이션

![KOLAS Target](https://img.shields.io/badge/KOLAS-2026_Target-blue) 
![CARLA](https://img.shields.io/badge/CARLA-Simulator-orange) 
![Architecture](https://img.shields.io/badge/Architecture-Two_Track-green) 
![Network](https://img.shields.io/badge/Network-Zero_Knowledge-purple)

**DAMOS(Decentralized Autonomous Mobility Simulation)** 프로젝트는 여러 대의 모빌리티(자율주행차, 배달 로봇 등)가 중앙 서버 없이 스스로 데이터를 교환하며 사각지대의 위험을 회피하는 **M2X 협력 자율주행 시스템**을 검증하기 위한 통합 시뮬레이터입니다.

---

## 🎯 핵심 공인 평가 지표 (KOLAS)
본 프로젝트는 시스템의 연산 병목을 막고 아래 두 가지 공인 인증 지표를 100% 달성하기 위해, **실시간 시뮬레이션(Track 1)**과 **오프라인 정량 평가(Track 2)**를 철저히 분리한 투트랙(Two-track) 아키텍처로 운영됩니다.

1. **18초 이내 연동 처리 속도 (Track 1):** 센서 데이터 추출 ➡️ 점유 영역 예측 및 융합 ➡️ M2X 통신 공유 ➡️ 차량 제어(정지/직진)까지의 1 Loop 연산이 18초 이내에 완료되어야 합니다.
2. **5초 이내 미래 궤적 예측 (Track 2):** 주변 객체가 향후 5초간 어떻게 움직일지 예측한 궤적(Trajectory)의 오차율을 평가합니다.

---
<img width="2816" height="1536" alt="Gemini_Generated_Image_jprperjprperjprp" src="https://github.com/user-attachments/assets/7776d5db-7b3d-4464-9c7f-2bb053deb6c3" />


## 🏗️ Two-Track 시스템 아키텍처 & 파이프라인

### 🏁 [Track 1] M2X 실시간 통합 시뮬레이션 (18초 지표 방어용)
모빌리티들이 통신으로 사각지대를 공유하고 사고를 피하는 핵심 데모 시나리오입니다. (무거운 궤적 예측 알고리즘 배제)

* **Step 1. 센서 데이터 추출 (메인 PC):** CARLA 서버가 각 모빌리티의 시야에서 **과거 6프레임의 이미지**를 추출하여 0.5초 단위로 서브 컴퓨터에 전달합니다.
* **Step 2. 로컬 점유 영역 예측 (서브 PC):** 6프레임 이미지를 입력받아, 내 시야 기준 **미래 10프레임(5초)의 점유 영역(Occupancy)**을 1차로 예측합니다.
* **Step 3. 영지식(ZK) M2X 통신 (서브 PC):** 내 로컬 예측 결과(압축 특징맵)와 현재 이동 정보를 영지식 증명 네트워크(Black Box)에 태워 다른 모빌리티들과 교환합니다.
* **Step 4. 글로벌 융합 (서브 PC):** 통신망을 통해 수신한 타 객체의 점유 영역 데이터와 내 로컬 데이터를 병합하여 **'사각지대 없는 글로벌 점유 영역 지도'**를 완성합니다.
* **Step 5. 룰 기반 제어 (메인 PC):** 완성된 점유 영역 지도상에 자차의 직진 경로가 겹치면(충돌 위험) **'긴급 제동(브레이크)'**, 겹치지 않으면 **'직진'** 하도록 가벼운 제어 명령을 내려 시뮬레이터에 반영합니다.

### 🔬 [Track 2] 5초 미래 궤적 예측 (오프라인 정량 평가용)
Track 1의 실시간 루프 연산 속도를 갉아먹지 않도록, 시뮬레이션 연동과 완전히 단절된 **단독 오프라인 연구**입니다.

* CARLA 메인 서버에서 추출한 정교한 **GT(Ground Truth)** 데이터를 활용하여 주변 객체의 미래 5초 궤적을 예측합니다. 
* 통합 시스템에 얹지 않고, 모델의 독자적인 오차율 결과만 추출하여 KOLAS 평가관에게 제출합니다.

---

## 👥 팀 R&R (역할 분담)

**🎮 시뮬레이션 통제 및 환경 구축 (메인 시스템)**
* **김기웅 (총괄):** CARLA 메인 동기화 루프 개발, 액터 소환(Spawning), 6프레임 이미지 버퍼링, GT 데이터 추출(Track 2 지원), Track 1의 10줄짜리 룰 기반 최종 제어(브레이크/직진) 로직 작성
* **손유정:** Scenic 기반 엣지 케이스 시나리오(사각지대, 무단횡단 등) 작성 및 CARLA 연동 무한 루프 구현
* **박형빈:** 배달 로봇, 안드로이드(보행자) 3D 커스텀 모델링 및 CARLA Blueprint 포팅

**🌐 Sub 1: 통신망 (Black Box)**
* **경다녕, 이서희, 염주현:** 영지식(ZKP) 기반 M2X 탈중앙형 네트워크 구축 (Track 1의 데이터 교환을 담당하며, 18초 이내 전송 보장)

**🧠 Sub 2 & 3: 인지/예측 및 융합**
* **이예린:** 과거 6프레임 이미지 기반 미래 10프레임(5초) 로컬 점유 영역 예측 모델 개발 (Track 1)
* **이성규:** 로컬 점유 영역 데이터와 수신된 타 모빌리티의 점유 영역 데이터를 융합하는 글로벌 매핑 로직 개발 (Track 1)
* **박준범:** CARLA GT 데이터 기반 5초 미래 궤적(Trajectory) 추출 모델 개발 (Track 2 전담, 통합 루프 제외)

---

## 🚀 Getting Started

### Prerequisites
* Ubuntu 22.04 / Windows 11
* CARLA Simulator 0.9.15
* Python 3.8+, PyTorch 2.0+

### Run the System
```bash
# 1. Start CARLA Server
cd /path/to/carla && ./CarlaUE4.sh

# 2. Run Main Synchronous Loop (Kiwoong)
python3 main_simulator.py

# 3. Run Sub Agents Pipeline (Track 1)
python3 run_agent_pipeline.py --role delivery_robot
