"""
config.py - car_config.py 설정값 모음
이 파일에서 모든 동작 파라미터를 관리합니다.

사용법:
    python car_config.py           # DEFAULT 사용
    python car_config.py ODIM      # ODIM 프로파일 오버라이드 적용
    python car_config.py odil      # 대소문자 무관
"""

from pathlib import Path

# =============================================================================
# DEFAULT 설정 (파라미터 미지정 시 사용)
# =============================================================================
DEFAULT = {
    # Task 활성화
    "ENABLED": {
        "edit_initial_setup":  True,
        "check_connectivity":  True,
        "install_logrotate":   True,
        "check_nic":           True,
    },

    # Task 1: initial_setup.sh 편집
    "INITIAL_SETUP_FILE": Path("/home/odin/initial_setup.sh"),
    "CAN_CMD_TEMPLATE": (
        "ip link set {iface} up type can bitrate 500000 sjw 3 sample-point 0.8"
        " berr-reporting on restart-ms 100"
    ),

    # Task 2: IP 연결 확인
    "PING_TARGETS": [
        "192.168.31.6",
        "192.168.31.7",
        "192.168.31.8",
        "192.168.0.6",
        "192.168.0.7",
        "192.168.0.8",
    ],
    "PING_COUNT":   3,
    "PING_TIMEOUT": 2,
    # (NIC 이름, IP 주소) 쌍
    # IP를 빈 문자열로 두면 인터페이스 UP 여부만 확인
    # 예: ("eth0", "192.168.1.1")  → IP 존재 및 NIC 일치 확인
    #     ("eth0", "")             → eth0 인터페이스 UP 여부만 확인
    "NIC_CHECKS": [
    ],
}

# =============================================================================
# 프로파일별 오버라이드 (DEFAULT에서 변경할 키만 작성)
# =============================================================================
PROFILES = {
    "odim": {
        "CAN_CMD_TEMPLATE": (
            "ip link set {iface} up type can bitrate 500000 sjw 3 sample-point 0.8"
            " dbitrate 2000000 berr-reporting on fd on restart-ms 100"
        ),
        "NIC_CHECKS": [
            ("eth0", "192.168.31.6"),
            ("veth0", "192.168.0.6"),
            ("veth0.2", "192.168.9.101"),
            ("can0", ""),
            ("can1", ""),
        ],
    },

    "odil": {
        "NIC_CHECKS": [
            ("eth0", "192.168.31.7"),
            ("veth0", "192.168.0.7"),
            ("veth0.2", "192.168.9.102"),
            ("veth0.30", "10.30.1.206"),
            ("veth0.34", "10.34.1.206"),
            ("veth0.35", "10.35.1.206"),
            ("veth0.37", "10.37.1.206"),
            ("can0", ""),
            ("can1", ""),
        ],
    },

    "odic": {
        "NIC_CHECKS": [
            ("eth0", "192.168.31.8"),
            ("veth0", "192.168.0.8"),
            ("veth0.2", "192.168.9.103"),
            ("veth0.30", "10.30.1.206"),
            ("veth0.34", "10.34.1.206"),
            ("veth0.35", "10.35.1.206"),
            ("veth0.37", "10.37.1.206"),
            ("can0", ""),
            ("can1", ""),
        ],
    },
}


def get_config(profile) -> dict:
    """DEFAULT에 프로파일 오버라이드를 병합한 설정 딕셔너리를 반환."""
    cfg = dict(DEFAULT)
    if profile is not None:
        key = profile.lower()
        if key not in PROFILES:
            raise ValueError(
                f"알 수 없는 프로파일: '{profile}'  (사용 가능: {', '.join(PROFILES)})"
            )
        cfg.update(PROFILES[key])
    return cfg
