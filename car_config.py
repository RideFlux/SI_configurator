#!/usr/bin/env python3
"""
setup.py - 시스템 초기 설정 스크립트
각 Task를 ENABLED 딕셔너리에서 True/False로 제어
"""

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Callable, Optional, Tuple

# =============================================================================
# 실행할 작업 선택 (True=실행, False=건너뜀)
# =============================================================================
ENABLED = {
    "edit_initial_setup":  True,
    "check_connectivity":  True,
    "install_logrotate":   True,
}

# =============================================================================
# 설정값
# =============================================================================
INITIAL_SETUP_FILE = Path("/home/odin/initial_setup.sh")

CAN_CMD_TEMPLATE = (
    "ip link set {iface} up type can bitrate 500000 "
    "dbitrate 2000000 berr-reporting on fd on"
)

PING_TARGETS = [
    "192.168.31.6",
    "192.168.31.7",
    "192.168.31.8",
    "192.168.0.6",
    "192.168.0.7",
    "192.168.0.8",
]

PING_COUNT   = 3   # 핑 횟수
PING_TIMEOUT = 2   # 타임아웃(초)


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

    lines = INITIAL_SETUP_FILE.read_text().splitlines(keepends=True)
    new_lines = []

    for line in lines:
        # 1) "state UP" → "UP" 치환
        line = line.replace("state UP", "UP")

        # 2) "ip link set canX" 를 포함한 줄 전체를 대치
        m = re.search(r"ip link set (can\d+)", line)
        if m:
            iface = m.group(1)
            # 줄 앞 공백(들여쓰기) 유지
            indent = re.match(r"^(\s*)", line).group(1)
            line = indent + CAN_CMD_TEMPLATE.format(iface=iface) + "\n"
            log("OK", f'  "{iface}" 줄 대치 완료')

        new_lines.append(line)

    INITIAL_SETUP_FILE.write_text("".join(new_lines))
    log("OK", '"state UP" → "UP" 치환 완료')
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

    pkg_managers = {
        "apt-get": ["apt-get", "install", "-y", "logrotate"],
        "yum":     ["yum",     "install", "-y", "logrotate"],
        "dnf":     ["dnf",     "install", "-y", "logrotate"],
    }

    for pm, install_cmd in pkg_managers.items():
        if shutil.which(pm):
            log("INFO", f"패키지 매니저 감지: {pm}")
            if pm == "apt-get":
                run_cmd(["apt-get", "update", "-qq"], check=False)
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
# Task 등록 테이블 (순서 보장, 새 Task는 여기에만 추가)
# =============================================================================
TASKS: List[Tuple[str, Callable]] = [
    ("edit_initial_setup", edit_initial_setup),
    ("check_connectivity", check_connectivity),
    ("install_logrotate",  install_logrotate),
]


# =============================================================================
# 메인
# =============================================================================
def main():
    print("=" * 50)
    print(f" 시스템 초기 설정 스크립트")
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
