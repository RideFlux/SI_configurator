# car_config

차량 시스템 초기 설정 스크립트. 네트워크, NIC, logrotate, LiDAR 설정을 프로파일 기반으로 자동화합니다.

## 파일 구성

```
car_config.py               # 메인 실행 스크립트
lidar_configurator.py       # Hesai PTC LiDAR 설정 스크립트
Makefile                    # 배포 자동화
configs/
├── config.yml              # 기본 설정 파일 (DEFAULT + 프로파일)
├── v7_config.yml           # v7 플랫폼 설정
├── v7_lidar_config.yaml    # v7 LiDAR 설정
├── v14_config.yml          # v14 플랫폼 설정
└── v14_lidar_config.yaml   # v14 LiDAR 설정
```

## 의존성

```bash
pip install pyyaml
```

---

## 실행

```bash
python3 car_config.py              # DEFAULT 프로파일
python3 car_config.py ODIM         # ODIM 프로파일 (대소문자 무관)
python3 car_config.py ODIL
python3 car_config.py ODIC

# 다른 설정 파일 지정
python3 car_config.py --config /path/to/custom.yml ODIM
```

### Task 목록

| Task | 내용 |
|------|------|
| `edit_initial_setup` | `initial_setup.sh`의 CAN 커맨드 및 state UP 수정 |
| `check_connectivity` | PING_TARGETS IP 연결 확인 |
| `install_logrotate` | logrotate 설치 확인 및 자동 설치 |
| `check_nic` | NIC 이름 및 IP 매핑 확인 |
| `run_lidar_config` | LiDAR 설정 (lidar_configurator.py 실행) |

---

## 설정 파일 (config.yml)

### 구조

```yaml
default:
  ENABLED:
    edit_initial_setup: true
    check_connectivity: true
    install_logrotate:  true
    check_nic:          true
    run_lidar_config:   false

  INITIAL_SETUP_FILE: /home/odin/initial_setup.sh
  CAN_CMD_TEMPLATE: "ip link set {iface} up type can ..."
  PING_TARGETS: [...]
  PING_COUNT:   3
  PING_TIMEOUT: 2
  NIC_CHECKS:   []
  LIDAR_CONFIG_FILE: null

profiles:
  odim:
    ENABLED:
      install_logrotate: false
    CAN_CMD_TEMPLATE: "ip link set {iface} up type can ... fd on ..."
    NIC_CHECKS:
      - [eth0, "192.168.31.6"]
      ...
```

### 프로파일 오버라이드 규칙

- `profiles` 아래에 변경할 키만 작성하면 `default`에 병합됩니다.
- `ENABLED` 같은 중첩 dict는 **키 단위로 병합**됩니다. 명시한 키만 override되고 나머지는 default 값을 유지합니다.

```yaml
# 예: odim에서 install_logrotate만 끄기
odim:
  ENABLED:
    install_logrotate: false   # 이 키만 false, 나머지는 default 유지
```

### LiDAR 설정

`LIDAR_CONFIG_FILE`에 LiDAR 설정 파일 경로를 지정하고 `run_lidar_config`를 활성화합니다.
경로는 절대 경로 또는 `config.yml` 기준 상대 경로를 사용합니다.

```yaml
odil:
  ENABLED:
    run_lidar_config: true
  LIDAR_CONFIG_FILE: v7_lidar_config.yaml
```

### NIC_CHECKS 형식

```yaml
NIC_CHECKS:
  - [eth0,  "192.168.31.6"]   # IP 존재 및 NIC 이름 확인
  - [can0,  ""]               # IP 생략 시 인터페이스 UP 여부만 확인
```

---

## 배포 (Makefile)

### 네트워크 구성

```
개발 머신 ──(VPN)──▶ odim (10.8.0.210)
                          │
                    (로컬망)
                     ├──▶ odil (192.168.31.7)
                     └──▶ odic (192.168.31.8)
```

개발 머신은 VPN을 통해 odim에만 직접 접근 가능합니다.
odil/odic는 odim을 경유하여 배포됩니다.

### Makefile 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ODIM_VPN` | `10.8.0.210` | odim VPN IP (개발 머신 → odim) |
| `PLATFORM` | (없음) | 플랫폼 prefix — 지정 시 CONFIG/LIDAR_CONFIG 자동 설정 |
| `CONFIG` | `configs/config.yml` | 차량 설정 파일 (PLATFORM 미지정 시 사용) |
| `LIDAR_CONFIG` | `configs/v7_lidar_config.yaml` | LiDAR 설정 파일 (PLATFORM 미지정 시 사용) |
| `USER` | `odin` | SSH 사용자 |
| `DEST` | `/home/odin/car_config` | 차량 내 설치 경로 |

### 배포 명령

```bash
# 기본값으로 배포 (configs/config.yml + configs/v7_lidar_config.yaml)
make deploy-odim
make deploy-odil
make deploy-odic
make deploy                              # 전체 (odim → odil → odic 순)

# ODIM_VPN 지정
make deploy      ODIM_VPN=10.8.0.100
make deploy-odil ODIM_VPN=10.8.0.100

# 플랫폼 지정 (configs/vX_config.yml + configs/vX_lidar_config.yaml 자동 선택)
make deploy      PLATFORM=v14
make deploy-odil PLATFORM=v16

# 여러 인수 조합
make deploy      PLATFORM=v14 ODIM_VPN=10.8.0.100

# 설정 파일 개별 지정 (PLATFORM 미지정 시)
make deploy-odil CONFIG=configs/custom.yml LIDAR_CONFIG=configs/v8_lidar_config.yaml
```

### 플랫폼별 자동 설정 파일

| `PLATFORM` | `CONFIG` | `LIDAR_CONFIG` |
|------------|----------|----------------|
| (기본값) | `configs/config.yml` | `configs/v7_lidar_config.yaml` |
| `v14` | `configs/v14_config.yml` | `configs/v14_lidar_config.yaml` |
| `v16` | `configs/v16_config.yml` | `configs/v16_lidar_config.yaml` |
| `vX` | `configs/vX_config.yml` | `configs/vX_lidar_config.yaml` |

### 정리

```bash
make clean         # 로컬 tar.gz 삭제
make clean-remote  # 원격 3대의 설치 경로 및 tar.gz 삭제
```

### 배포 흐름

```
make deploy-odil PLATFORM=v14 실행 시:

1. 로컬에서 tar.gz 생성
   (car_config.py, lidar_configurator.py, configs/v14_config.yml, configs/v14_lidar_config.yaml)
2. scp → odim:~/car_config.tar.gz
3. odim에서 scp → odil:~/car_config.tar.gz
4. odil에서 tar 압축 해제 → /home/odin/car_config/
5. odil에서 python3 car_config.py --config /home/odin/car_config/configs/v14_config.yml ODIL 실행
```
