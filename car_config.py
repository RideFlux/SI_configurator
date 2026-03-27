#!/usr/bin/env python3
"""
car_config.py - 시스템 초기 설정 스크립트
각 Task를 ENABLED 딕셔너리에서 True/False로 제어

사용법:
    python car_config.py                              # config.yml, DEFAULT
    python car_config.py ODIM                         # config.yml, ODIM 프로파일
    python car_config.py --config /path/to/my.yml     # 다른 설정 파일, DEFAULT
    python car_config.py --config /path/to/my.yml ODIM
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Callable, List, Optional, Tuple

_DEFAULT_CONFIG = Path(__file__).parent / "configs" / "config.yml"


def _deep_merge(base: dict, override: dict) -> dict:
    """override를 base에 재귀적으로 병합한 새 dict를 반환. 중첩 dict는 키 단위로 병합."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_config(config_file: Path, profile: Optional[str]) -> dict:
    """YAML 설정 파일을 읽어 DEFAULT에 프로파일 오버라이드를 병합한 딕셔너리를 반환."""
    if not config_file.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_file}")

    with config_file.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    cfg = dict(data["default"])

    if profile is not None:
        key = profile.lower()
        profiles = data.get("profiles", {})
        if key not in profiles:
            raise ValueError(
                f"알 수 없는 프로파일: '{profile}'  (사용 가능: {', '.join(profiles)})"
            )
        cfg = _deep_merge(cfg, profiles[key])

    cfg["INITIAL_SETUP_FILE"] = Path(cfg["INITIAL_SETUP_FILE"])
    cfg["NIC_CHECKS"] = [tuple(pair) for pair in cfg["NIC_CHECKS"]]

    lidar_file = cfg.get("LIDAR_CONFIG_FILE")
    if lidar_file:
        p = Path(lidar_file)
        cfg["LIDAR_CONFIG_FILE"] = p if p.is_absolute() else config_file.parent / p
    else:
        cfg["LIDAR_CONFIG_FILE"] = None

    return cfg


# 모듈 전역 설정 — main()의 _apply_config() 호출 후 실제 값으로 채워짐
ENABLED:            dict          = {}
INITIAL_SETUP_FILE: Path          = Path()
CAN_CMD_TEMPLATE:   str           = ""
PING_TARGETS:       list          = []
PING_COUNT:         int           = 0
PING_TIMEOUT:       int           = 0
NIC_CHECKS:         list          = []
LIDAR_CONFIG_FILE:  Optional[Path] = None


def _apply_config(config_file: Path, profile: Optional[str]):
    """설정 파일과 프로파일을 모듈 전역 변수에 반영한다."""
    g = globals()
    for key, val in _load_config(config_file, profile).items():
        g[key] = val


# =============================================================================
# 공통 유틸
# =============================================================================
class Color:
    RESET  = "\033[0m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    BOLD   = "\033[1m"

def log(level: str, msg: str):
    tag, color = {
        "INFO":  ("[INFO] ", Color.CYAN),
        "OK":    ("[OK]   ", Color.GREEN),
        "WARN":  ("[WARN] ", Color.YELLOW),
        "ERROR": ("[ERROR]", Color.RED),
        "SKIP":  ("[SKIP] ", Color.YELLOW),
    }.get(level, ("[LOG]  ", Color.RESET))
    print(f"{color}{tag}{Color.RESET} {msg}")

def separator():
    print("─" * 50)

def run_cmd(cmd: List[str], check=True) -> subprocess.CompletedProcess:
    """명령어 실행 후 결과 반환. 실패 시 CalledProcessError 발생"""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


# =============================================================================
# Task 1: initial_setup.sh 편집
# =============================================================================
def edit_initial_setup():
    separator()
    log("INFO", f"initial_setup.sh 편집 시작: {INITIAL_SETUP_FILE}")

    if not INITIAL_SETUP_FILE.exists():
        log("ERROR", f"파일이 존재하지 않습니다: {INITIAL_SETUP_FILE}")
        return False

    original_text = INITIAL_SETUP_FILE.read_text()
    lines = original_text.splitlines(keepends=True)
    new_lines = []

    for line in lines:
        if "state UP" in line:
            line = line.replace("state UP", "UP")
            log("OK", f'  "state UP" → "UP" 치환')

        m = re.search(r"ip link set (can\d+)", line)
        if m:
            iface = m.group(1)
            indent = re.match(r"^(\s*)", line).group(1)
            new_line = indent + CAN_CMD_TEMPLATE.format(iface=iface) + "\n"
            if line == new_line:
                log("INFO", f'  "{iface}" 줄 이미 동일 — 건너뜀')
            else:
                line = new_line
                log("OK", f'  "{iface}" 줄 대치 완료')

        new_lines.append(line)

    new_text = "".join(new_lines)
    if new_text == original_text:
        log("INFO", "변경 사항 없음 — 백업 및 저장 생략")
    else:
        backup = INITIAL_SETUP_FILE.with_suffix(
            f".sh.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(INITIAL_SETUP_FILE, backup)
        log("INFO", f"백업 생성: {backup}")
        INITIAL_SETUP_FILE.write_text(new_text)
        log("OK", "편집 완료")

    return True


# =============================================================================
# Task 2: IP 연결 확인
# =============================================================================
def check_connectivity():
    separator()
    log("INFO", f"IP 연결 확인 시작 (ping ×{PING_COUNT}, timeout {PING_TIMEOUT}s)")

    if not PING_TARGETS:
        log("WARN", "PING_TARGETS가 비어 있습니다 — 건너뜀")
        return False

    results: Dict[str, bool] = {}

    for ip in PING_TARGETS:
        try:
            run_cmd(["ping", "-c", str(PING_COUNT), "-W", str(PING_TIMEOUT), ip])
            log("OK",   f"REACHABLE   {ip}")
            results[ip] = True
        except subprocess.CalledProcessError:
            log("WARN", f"UNREACHABLE {ip}")
            results[ip] = False

    separator()
    passed = sum(results.values())
    failed = len(results) - passed
    log("INFO", f"결과: 성공 {passed}개 / 실패 {failed}개 (전체 {len(results)}개)")

    if failed:
        log("WARN", "일부 IP에 연결 불가 — 네트워크 설정을 확인하세요")
        return False

    log("OK", "모든 IP 연결 확인 완료")
    return True


# =============================================================================
# Task 3: logrotate 설치
# =============================================================================
def install_logrotate():
    separator()
    log("INFO", "logrotate 설치 확인")

    if shutil.which("logrotate"):
        ver = run_cmd(["logrotate", "--version"], check=False).stdout.splitlines()
        log("OK", f"이미 설치되어 있습니다: {ver[0] if ver else 'logrotate'}")
        return True

    log("INFO", "logrotate 설치 중...")

    sudo = ["sudo"] if shutil.which("sudo") and hasattr(os, "geteuid") and os.geteuid() != 0 else []

    pkg_managers = {
        "apt-get": sudo + ["apt-get", "install", "-y", "logrotate"],
        "yum":     sudo + ["yum",     "install", "-y", "logrotate"],
        "dnf":     sudo + ["dnf",     "install", "-y", "logrotate"],
    }

    for pm, install_cmd in pkg_managers.items():
        if shutil.which(pm):
            log("INFO", f"패키지 매니저 감지: {pm}")
            if pm == "apt-get":
                run_cmd(sudo + ["apt-get", "update", "-qq"], check=False)
            try:
                run_cmd(install_cmd)
                log("OK", "logrotate 설치 완료")
                return True
            except subprocess.CalledProcessError as e:
                log("ERROR", f"설치 실패: {e.stderr.strip()}")
                return False

    log("ERROR", "지원하는 패키지 매니저를 찾을 수 없습니다")
    return False


# =============================================================================
# Task 4: NIC 이름 및 IP 확인
# =============================================================================
def _build_ip_iface_map() -> Dict[str, str]:
    """시스템 전체 IP → 인터페이스 이름 맵을 한 번만 조회해 반환."""
    result = run_cmd(["ip", "-o", "addr"], check=False)
    ip_map: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        # 형식: "2: eth0    inet 192.168.1.100/24 ..."
        m = re.search(r"\binet6?\s+([^\s/]+)", line)
        if m:
            parts = line.split()
            if len(parts) >= 2:
                ip_map[m.group(1)] = parts[1]
    return ip_map


def _is_iface_up(iface: str) -> Optional[bool]:
    """인터페이스가 존재하면 UP 여부(True/False)를 반환. 없으면 None."""
    result = run_cmd(["ip", "link", "show", iface], check=False)
    if result.returncode != 0:
        return None
    return "state UP" in result.stdout


def check_nic():
    separator()
    log("INFO", f"NIC 확인 ({len(NIC_CHECKS)}개 항목)")

    ip_iface_map = _build_ip_iface_map()
    all_ok = True
    for expected_iface, ip in NIC_CHECKS:
        if ip:
            actual_iface = ip_iface_map.get(ip)
            if actual_iface is None:
                log("ERROR", f"IP 없음: {ip} (예상 NIC: {expected_iface})")
                all_ok = False
                continue

            state = _is_iface_up(actual_iface)
            up_tag = "UP" if state else "DOWN"

            if actual_iface != expected_iface:
                log("WARN",  f"NIC 다름: {ip}  예상={expected_iface}  실제={actual_iface}  [{up_tag}]")
            else:
                log("OK" if state else "ERROR",
                    f"IP 확인됨: {ip} → {actual_iface}  [{up_tag}]")

            if not state:
                all_ok = False
        else:
            state = _is_iface_up(expected_iface)
            if state is None:
                log("ERROR", f"인터페이스 없음: {expected_iface}")
                all_ok = False
            elif state:
                log("OK",    f"UP 확인됨: {expected_iface}")
            else:
                log("ERROR", f"DOWN 상태: {expected_iface}")
                all_ok = False

    return all_ok


# =============================================================================
# Task 5: LiDAR 설정
# =============================================================================
def run_lidar_config():
    separator()
    log("INFO", f"LiDAR 설정 시작: {LIDAR_CONFIG_FILE}")

    if not LIDAR_CONFIG_FILE:
        log("WARN", "LIDAR_CONFIG_FILE이 설정되지 않았습니다 — 건너뜀")
        return False

    if not LIDAR_CONFIG_FILE.exists():
        log("ERROR", f"LiDAR 설정 파일이 존재하지 않습니다: {LIDAR_CONFIG_FILE}")
        return False

    _lidar_script = Path(__file__).parent / "lidar_configurator.py"
    if not _lidar_script.exists():
        log("ERROR", f"lidar_configurator.py를 찾을 수 없습니다: {_lidar_script}")
        return False

    result = subprocess.run(
        [sys.executable, str(_lidar_script), "--config", str(LIDAR_CONFIG_FILE)],
    )
    if result.returncode == 0:
        log("OK", "LiDAR 설정 완료")
    else:
        log("ERROR", f"lidar_configurator.py 종료 코드: {result.returncode}")
    return result.returncode == 0


# =============================================================================
# Task 등록 테이블 (순서 보장, 새 Task는 여기에만 추가)
# =============================================================================
TASKS: List[Tuple[str, Callable]] = [
    ("edit_initial_setup", edit_initial_setup),
    ("check_connectivity", check_connectivity),
    ("install_logrotate",  install_logrotate),
    ("check_nic",          check_nic),
    ("run_lidar_config",   run_lidar_config),
]


# =============================================================================
# 메인
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="시스템 초기 설정 스크립트")
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        metavar="FILE",
        help=f"YAML 설정 파일 경로 (기본값: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "profile",
        nargs="?",
        default=None,
        metavar="PROFILE",
        help="설정 프로파일 (ODIM / ODIL / ODIC, 대소문자 무관). 생략 시 DEFAULT 사용.",
    )
    args = parser.parse_args()

    try:
        _apply_config(args.config, args.profile)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}")
        return 1

    profile_label = args.profile.upper() if args.profile else "DEFAULT"

    print("=" * 50)
    print(f" 시스템 초기 설정 스크립트  [{profile_label}]")
    print(f" 설정 파일: {args.config}")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    results: Dict[str, Optional[bool]] = {}

    for name, func in TASKS:
        if not ENABLED.get(name, False):
            log("SKIP", f"{name} (비활성)")
            results[name] = None
            continue
        results[name] = func()

    # ── 최종 요약 ──────────────────────────────────────────────────────────
    separator()
    print(f"{Color.BOLD}[요약]{Color.RESET}")
    all_ok = True
    for name, result in results.items():
        if result is None:
            print(f"  {Color.YELLOW}SKIP {Color.RESET} {name}")
        elif result:
            print(f"  {Color.GREEN}OK   {Color.RESET} {name}")
        else:
            print(f"  {Color.RED}FAIL {Color.RESET} {name}")
            all_ok = False

    separator()
    if all_ok:
        log("OK", "모든 작업 완료")
    else:
        log("WARN", "일부 작업에서 오류가 발생했습니다")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
