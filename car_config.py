#!/usr/bin/env python3
"""
setup.py - 시스템 초기 설정 스크립트
각 Task를 ENABLED 딕셔너리에서 True/False로 제어
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import config as _cfg

# 모듈 전역 설정 — main()에서 프로파일 적용 후 갱신됨
_c = _cfg.get_config(None)
ENABLED            = _c["ENABLED"]
INITIAL_SETUP_FILE = _c["INITIAL_SETUP_FILE"]
CAN_CMD_TEMPLATE   = _c["CAN_CMD_TEMPLATE"]
PING_TARGETS       = _c["PING_TARGETS"]
PING_COUNT         = _c["PING_COUNT"]
PING_TIMEOUT       = _c["PING_TIMEOUT"]
NIC_CHECKS         = _c["NIC_CHECKS"]
del _c


def _apply_profile(profile: Optional[str]):
    """프로파일 설정을 모듈 전역 변수에 반영한다."""
    g = globals()
    for key, val in _cfg.get_config(profile).items():
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

    # 백업
    backup = INITIAL_SETUP_FILE.with_suffix(
        f".sh.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(INITIAL_SETUP_FILE, backup)
    log("INFO", f"백업 생성: {backup}")

    original_text = INITIAL_SETUP_FILE.read_text()
    lines = original_text.splitlines(keepends=True)
    new_lines = []

    for line in lines:
        # 1) "state UP" 가 있을 때만 "UP" 으로 치환
        if "state UP" in line:
            line = line.replace("state UP", "UP")
            log("OK", f'  "state UP" → "UP" 치환')

        # 2) "ip link set canX" 를 포함한 줄 전체를 대치 (동일하면 건너뜀)
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

    results: dict[str, bool] = {}

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

    sudo = ["sudo"] if shutil.which("sudo") and os.geteuid() != 0 else []

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
def _find_iface_for_ip(ip: str) -> Optional[str]:
    """시스템 전체에서 ip가 할당된 인터페이스 이름을 반환. 없으면 None."""
    result = run_cmd(["ip", "-o", "addr"], check=False)
    for line in result.stdout.splitlines():
        # 형식: "2: eth0    inet 192.168.1.100/24 ..."
        m = re.search(rf"\binet6?\s+{re.escape(ip)}(?:/|\s)", line)
        if m:
            parts = line.split()
            return parts[1] if len(parts) >= 2 else None
    return None


def _is_iface_up(iface: str) -> Optional[bool]:
    """인터페이스가 존재하면 UP 여부(True/False)를 반환. 없으면 None."""
    result = run_cmd(["ip", "link", "show", iface], check=False)
    if result.returncode != 0:
        return None
    return "state UP" in result.stdout


def check_nic():
    separator()
    log("INFO", f"NIC 확인 ({len(NIC_CHECKS)}개 항목)")

    all_ok = True
    for expected_iface, ip in NIC_CHECKS:
        if not ip:
            # IP 미지정 — 인터페이스 UP 여부만 확인
            state = _is_iface_up(expected_iface)
            if state is None:
                log("ERROR", f"인터페이스 없음: {expected_iface}")
                all_ok = False
            elif state:
                log("OK",   f"UP 확인됨: {expected_iface}")
            else:
                log("ERROR", f"DOWN 상태: {expected_iface}")
                all_ok = False
        else:
            # IP 지정 — IP 존재 여부 및 NIC 일치 확인
            actual_iface = _find_iface_for_ip(ip)
            if actual_iface is None:
                log("ERROR", f"IP 없음: {ip} (예상 NIC: {expected_iface})")
                all_ok = False
            elif actual_iface != expected_iface:
                log("WARN",  f"IP 존재하나 NIC 다름: {ip}  예상={expected_iface}  실제={actual_iface}")
            else:
                log("OK",    f"IP 확인됨: {ip} → {actual_iface}")

    return all_ok


# =============================================================================
# Task 등록 테이블 (순서 보장, 새 Task는 여기에만 추가)
# =============================================================================
TASKS: List[Tuple[str, Callable]] = [
    ("edit_initial_setup", edit_initial_setup),
    ("check_connectivity", check_connectivity),
    ("install_logrotate",  install_logrotate),
    ("check_nic",          check_nic),
]


# =============================================================================
# 메인
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="시스템 초기 설정 스크립트")
    parser.add_argument(
        "profile",
        nargs="?",
        default=None,
        metavar="PROFILE",
        help="설정 프로파일 (ODIM / ODIL / ODIC, 대소문자 무관). 생략 시 DEFAULT 사용.",
    )
    args = parser.parse_args()

    try:
        _apply_profile(args.profile)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    profile_label = args.profile.upper() if args.profile else "DEFAULT"

    print("=" * 50)
    print(f" 시스템 초기 설정 스크립트  [{profile_label}]")
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
