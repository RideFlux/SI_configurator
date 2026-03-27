"""
Hesai PTC Protocol - Multi LiDAR Configurator

설정 파일 구조:
  connection : 스크립트 실행에 필요한 접속 정보 (ip, port, timeout)
  settings   : LiDAR에 적용할 설정값

실행 흐름:
  1. LiDAR 접속 → 전체 상태 리포트 출력
  2. settings 값과 다른 항목 표시
  3. 변경할지 질문 → y 입력 시 적용

※ Clock Source (GPS↔PTP) 변경은 PTC 프로토콜에 전용 커맨드가 없습니다.
   Web Control에서 수동으로 변경 후 스크립트를 사용하세요.

Usage:
    python hesai_ptc_configurator.py --config config.yaml
    python hesai_ptc_configurator.py --config config.yaml --yes
    python hesai_ptc_configurator.py --config config.yaml --only sensor_front
    python hesai_ptc_configurator.py --generate-sample
"""

import socket
import struct
import argparse
import json
import sys
import os

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_PORT    = 9347
DEFAULT_TIMEOUT = 5.0
HEADER_MAGIC    = bytes([0x47, 0x74])
HDR_LEN         = 8

CMD_GET_CONFIG_INFO    = 0x08
CMD_GET_LIDAR_STATUS   = 0x09
CMD_SET_TRIGGER_METHOD = 0x1B
CMD_SET_RETURN_MODE    = 0x1E
CMD_SET_DESTINATION_IP = 0x20
CMD_SET_PTP_CONFIG     = 0x24
CMD_GET_PTP_CONFIG     = 0x26

RETURN_CODES = {
    0x00: "No error",           0x01: "Invalid input parameter",
    0x02: "Failure to connect", 0x03: "No valid data returned",
    0x04: "Not enough memory",  0x05: "Command not supported",
    0x06: "Inner FPGA error",
}

TRIGGER_METHOD_MAP = {"angle": 0, "angle based": 0, "time": 1, "time based": 1}
RETURN_MODE_MAP    = {"last": 0, "last return": 0, "dual": 2, "dual return": 2, "first": 3, "first return": 3}
CLOCK_SOURCE_MAP   = {"gps": 0, "ptp": 1}
PTP_PROFILE_MAP    = {"ieee1588v2": 0, "1588v2": 0, "ieee802.1as": 1, "802.1as": 1}
PTP_NETWORK_MAP    = {"udp": 0, "udp/ip": 0, "l2": 1}

TRIGGER_METHOD_INV = {0: "angle based",  1: "time based"}
RETURN_MODE_INV    = {0: "last return",  2: "dual return",  3: "first return"}
CLOCK_SOURCE_INV   = {0: "GPS",          1: "PTP"}
PTP_PROFILE_INV    = {0: "IEEE 1588v2",  1: "IEEE 802.1AS"}
PTP_NETWORK_INV    = {0: "UDP/IP",       1: "L2"}
PTP_CLOCK_STATUS   = {0: "Free run",     1: "Tracking",     2: "Locked",  3: "Frozen"}


GREEN   = "\033[92m"; RED    = "\033[91m"; YELLOW = "\033[93m"
CYAN    = "\033[96m"; RESET  = "\033[0m";  BOLD   = "\033[1m"
MAGENTA = "\033[95m"; DIM    = "\033[2m"

# ── 샘플 설정 파일 ─────────────────────────────────────────────────────────────

SAMPLE_CONFIG_YAML = """\
# =============================================================================
# Hesai PTC LiDAR 설정 파일
# =============================================================================
defaults:
  connection:
    port: 9347
    timeout: 5.0
  settings:
    trigger_method: "time based"      # angle based | time based
    return_mode: "dual return"        # last return | dual return | first return
    dest_lidar_udp_port: 2368
    clock_source: "PTP"               # GPS | PTP  ※ 변경 불가 (Web Control 사용)
    ptp:
      profile: "ieee1588v2"           # ieee1588v2 | ieee802.1as
      domain: 0                       # 0~127
      network: "UDP/IP"               # UDP/IP | L2
      logAnnounceInterval: 1          # -2~3
      logSyncInterval: 1              # -7~3
      logMinDelayReqInterval: 0       # -7~3

lidars:
  - name: sensor_front
    connection:
      ip: "192.168.1.201"

  - name: sensor_rear
    connection:
      ip: "192.168.1.202"
    settings:
      return_mode: "last return"
      dest_lidar_udp_port: 2369

  - name: sensor_left
    connection:
      ip: "192.168.1.203"
      timeout: 8.0
    settings:
      trigger_method: "angle based"
      clock_source: "GPS"
"""

SAMPLE_CONFIG_JSON = {
    "defaults": {
        "connection": {"port": 9347, "timeout": 5.0},
        "settings": {
            "trigger_method": "time based", "return_mode": "dual return",
            "dest_lidar_udp_port": 2368, "clock_source": "PTP",
            "ptp": {"profile": "ieee1588v2", "domain": 0, "network": "UDP/IP",
                    "logAnnounceInterval": 1, "logSyncInterval": 1,
                    "logMinDelayReqInterval": 0}
        }
    },
    "lidars": [
        {"name": "sensor_front", "connection": {"ip": "192.168.1.201"}},
        {"name": "sensor_rear",  "connection": {"ip": "192.168.1.202"},
         "settings": {"return_mode": "last return", "dest_lidar_udp_port": 2369}},
        {"name": "sensor_left",  "connection": {"ip": "192.168.1.203", "timeout": 8.0},
         "settings": {"trigger_method": "angle based", "clock_source": "GPS"}},
    ]
}


def generate_sample_config():
    if YAML_AVAILABLE:
        path = "lidar_config.yaml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(SAMPLE_CONFIG_YAML)
    else:
        path = "lidar_config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_CONFIG_JSON, f, indent=2, ensure_ascii=False)
        print("※ JSON은 주석을 지원하지 않습니다.")
    print(f"샘플 설정 파일 생성: {path}")


def load_config(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as f:
        if ext in (".yaml", ".yml"):
            if not YAML_AVAILABLE:
                print("[ERROR] `pip install pyyaml` 을 먼저 실행하세요."); sys.exit(1)
            return yaml.safe_load(f)
        elif ext == ".json":
            return json.load(f)
        else:
            print(f"[ERROR] 지원하지 않는 파일 형식: {ext}"); sys.exit(1)


def deep_merge(base, override):
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def norm(v):
    return v.lower().strip() if isinstance(v, str) else v


def resolve_lidar(entry, defaults):
    def_conn = defaults.get("connection", {})
    ent_conn = entry.get("connection", {})
    conn = deep_merge(def_conn, ent_conn)
    if "ip" not in conn:
        raise ValueError(f"LiDAR '{entry.get('name', '?')}' 에 ip가 없습니다.")
    return {
        "name":     entry.get("name", conn["ip"]),
        "ip":       conn["ip"],
        "port":     conn.get("port",    DEFAULT_PORT),
        "timeout":  conn.get("timeout", DEFAULT_TIMEOUT),
        "settings": deep_merge(defaults.get("settings", {}), entry.get("settings", {})),
    }


# ── 네트워크 헬퍼 ──────────────────────────────────────────────────────────────

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("소켓 연결 끊김")
        buf += chunk
    return buf


def send_command(ip, port, cmd, payload=b"", timeout=5.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect((ip, port))
        pkt = HEADER_MAGIC + bytes([cmd, 0x00]) + struct.pack(">I", len(payload)) + payload
        s.sendall(pkt)
        hdr = recv_exact(s, HDR_LEN)
        if hdr[:2] != HEADER_MAGIC:
            raise ValueError(f"Bad magic: {hdr[:2].hex()}")
        ret_code = hdr[3]
        pay_len  = struct.unpack(">I", hdr[4:8])[0]
        if ret_code:
            raise RuntimeError(f"LiDAR 오류 0x{ret_code:02X}: {RETURN_CODES.get(ret_code, '?')}")
        return recv_exact(s, pay_len) if pay_len else b""


# ── 파서 ──────────────────────────────────────────────────────────────────────

def parse_config_info(data):
    c = {}; o = 0
    c["ipaddr"]  = ".".join(str(data[o+i]) for i in range(4)); o+=4
    c["mask"]    = ".".join(str(data[o+i]) for i in range(4)); o+=4
    c["gateway"] = ".".join(str(data[o+i]) for i in range(4)); o+=4
    c["dest_ip"] = ".".join(str(data[o+i]) for i in range(4)); o+=4
    c["dest_lidar_udp_port"] = struct.unpack_from(">H", data, o)[0]; o+=2
    c["dest_gps_udp_port"]   = struct.unpack_from(">H", data, o)[0]; o+=2
    c["spin_rate"]           = struct.unpack_from(">H", data, o)[0]; o+=2
    c["sync"]       = data[o]; o+=1
    c["sync_angle"] = struct.unpack_from(">H", data, o)[0] * 0.01; o+=2
    o += 4
    c["clock_source"]   = data[o]; o+=1
    c["udp_seq"]        = data[o]; o+=1
    c["trigger_method"] = data[o]; o+=1
    c["return_mode"]    = data[o]; o+=1
    c["standby_mode"]   = data[o]; o+=1
    c["motor_status"]   = data[o]; o+=1
    c["vlan_flag"]      = data[o]; o+=1
    c["vlan_id"]        = struct.unpack_from(">H", data, o)[0]; o+=2
    return c


def parse_ptp_config(data):
    p = {}; o = 0
    p["status"]  = data[o]; o+=1
    p["profile"] = data[o]; o+=1
    p["domain"]  = data[o]; o+=1
    p["network"] = data[o]; o+=1
    if p["profile"] == 0 and len(data) >= 7:
        p["logAnnounceInterval"]    = struct.unpack_from("b", data, o)[0]; o+=1
        p["logSyncInterval"]        = struct.unpack_from("b", data, o)[0]; o+=1
        p["logMinDelayReqInterval"] = struct.unpack_from("b", data, o)[0]
    return p


def parse_lidar_status(data):
    st = {}; o = 0
    st["system_uptime"]    = struct.unpack_from(">I", data, o)[0]; o+=4
    st["motor_speed_rpm"]  = struct.unpack_from(">H", data, o)[0]; o+=2
    temps = [struct.unpack_from(">i", data, o + i*4)[0] * 0.01 for i in range(8)]; o+=32
    st["temperatures"]         = temps
    st["gps_pps_lock"]         = bool(data[o]); o+=1
    st["gps_gprmc_status"]     = bool(data[o]); o+=1
    st["startup_times"]        = struct.unpack_from(">I", data, o)[0]; o+=4
    st["total_operation_time"] = struct.unpack_from(">I", data, o)[0]; o+=4
    st["ptp_clock_status"]     = PTP_CLOCK_STATUS.get(data[o], f"Unknown({data[o]})")
    return st


# ── 커맨드 빌더 ────────────────────────────────────────────────────────────────

def build_set_destination_ip(dest_ip, lidar_port, gps_port):
    return bytes(int(x) for x in dest_ip.split(".")) + struct.pack(">HH", lidar_port, gps_port)

def build_ptp_1588v2(domain, network, ann, sync_i, delay):
    return bytes([0, domain, network]) + struct.pack("bbb", ann, sync_i, delay)

def build_ptp_8021as(domain):
    return bytes([1, domain, 1])


# ── Diff ──────────────────────────────────────────────────────────────────────

class Diff:
    def __init__(self, label, current, desired, apply_fn):
        self.label    = label
        self.current  = current
        self.desired  = desired
        self.apply_fn = apply_fn  # None이면 경고 표시 전용


# ── 핵심: LiDAR 조회 + 리포트 + diff 수집 ────────────────────────────────────

def process_lidar(lidar):
    ip      = lidar["ip"]
    port    = lidar["port"]
    timeout = lidar["timeout"]
    s       = lidar.get("settings", {})

    result = {"name": lidar["name"], "ip": ip, "report": [], "diffs": [], "error": None}

    # ── 조회 ──────────────────────────────────────────────────────────────
    try:
        cur_cfg = parse_config_info(send_command(ip, port, CMD_GET_CONFIG_INFO, timeout=timeout))
    except Exception as e:
        result["error"] = f"CONFIG 조회 실패: {e}"; return result

    try:
        cur_ptp = parse_ptp_config(send_command(ip, port, CMD_GET_PTP_CONFIG, timeout=timeout))
    except Exception:
        cur_ptp = {}

    try:
        cur_st = parse_lidar_status(send_command(ip, port, CMD_GET_LIDAR_STATUS, timeout=timeout))
    except Exception:
        cur_st = {}

    # ── 헬퍼 ──────────────────────────────────────────────────────────────
    def row(label, cur_val, want_val=None, apply_fn=None):
        mismatch = (want_val is not None) and (cur_val != want_val)
        result["report"].append({"label": label, "current": cur_val,
                                  "expected": want_val, "mismatch": mismatch})
        if mismatch and apply_fn:
            result["diffs"].append(Diff(label, cur_val, want_val, apply_fn))

    def section(title):
        result["report"].append({"label": None, "section": title})

    def warn(msg):
        """경고 메시지를 리포트와 diffs(apply_fn=None)에 추가"""
        result["report"].append({"label": f"  ※ 경고", "current": msg,
                                  "expected": None, "mismatch": False})
        result["diffs"].append(Diff("⚠ 수동 변경 필요", msg, "", None))

    # ── [ Config ] ────────────────────────────────────────────────────────
    section("[ Config ]")

    wi = TRIGGER_METHOD_MAP.get(norm(s.get("trigger_method", "")))
    row("Trigger Method",
        TRIGGER_METHOD_INV.get(cur_cfg["trigger_method"], str(cur_cfg["trigger_method"])),
        TRIGGER_METHOD_INV[wi] if wi is not None else None,
        (lambda v=wi: send_command(ip, port, CMD_SET_TRIGGER_METHOD, bytes([v]), timeout))
        if wi is not None else None)

    wi = RETURN_MODE_MAP.get(norm(s.get("return_mode", "")))
    row("Return Mode",
        RETURN_MODE_INV.get(cur_cfg["return_mode"], str(cur_cfg["return_mode"])),
        RETURN_MODE_INV[wi] if wi is not None else None,
        (lambda v=wi: send_command(ip, port, CMD_SET_RETURN_MODE, bytes([v]), timeout))
        if wi is not None else None)

    want_port = int(s["dest_lidar_udp_port"]) if "dest_lidar_udp_port" in s else None
    row("Dest LiDAR UDP Port", cur_cfg["dest_lidar_udp_port"], want_port,
        (lambda wp=want_port: send_command(ip, port, CMD_SET_DESTINATION_IP,
            build_set_destination_ip(cur_cfg["dest_ip"], wp, cur_cfg["dest_gps_udp_port"]),
            timeout)) if want_port is not None else None)

    # clock_source: PTC 프로토콜에 전용 변경 커맨드 없음 → 불일치 시 경고만
    cur_cs      = cur_cfg["clock_source"]
    cur_cs_str  = CLOCK_SOURCE_INV.get(cur_cs, str(cur_cs))
    wi_cs       = CLOCK_SOURCE_MAP.get(norm(s.get("clock_source", "")))
    want_cs_str = CLOCK_SOURCE_INV[wi_cs] if wi_cs is not None else None
    cs_mismatch = (wi_cs is not None) and (cur_cs != wi_cs)
    result["report"].append({"label": "Clock Source", "current": cur_cs_str,
                              "expected": want_cs_str, "mismatch": cs_mismatch})
    if cs_mismatch:
        warn(f"Clock Source {cur_cs_str}→{want_cs_str} 변경은 Web Control에서 수동으로 해주세요")

    row("Spin Rate (RPM)", cur_cfg["spin_rate"])
    row("Standby Mode",    "Standby" if cur_cfg["standby_mode"] else "In operation")
    row("UDP Seq",         cur_cfg["udp_seq"])
    row("Sync",            "Enabled" if cur_cfg["sync"] else "Disabled")
    row("Sync Angle (°)",  f"{cur_cfg['sync_angle']:.2f}")
    row("VLAN",            f"{'ON' if cur_cfg['vlan_flag'] else 'OFF'}  ID={cur_cfg['vlan_id']}")
    row("Motor Direction", "CCW" if (cur_cfg["motor_status"] & 0x01) else "CW")

    # ── [ PTP Config ] ────────────────────────────────────────────────────
    section("[ PTP Config ]")

    if cur_ptp:
        # clock_source가 GPS인 경우 PTP 설정 변경 불가 → 비교만 하고 적용 안 함
        ptp_applicable = (cur_cs == 1)  # 현재 LiDAR가 PTP 모드일 때만 적용
        ptp_s = s.get("ptp", {})

        wp  = PTP_PROFILE_MAP.get(norm(ptp_s.get("profile", ""))) if ptp_s else None
        wd  = int(ptp_s["domain"])                                 if "domain"  in ptp_s else None
        wn  = PTP_NETWORK_MAP.get(norm(ptp_s.get("network", ""))) if ptp_s else None
        if wp == 1: wn = 1
        wa  = int(ptp_s["logAnnounceInterval"])    if "logAnnounceInterval"    in ptp_s else None
        ws  = int(ptp_s["logSyncInterval"])        if "logSyncInterval"        in ptp_s else None
        wdl = int(ptp_s["logMinDelayReqInterval"]) if "logMinDelayReqInterval" in ptp_s else None

        cur_profile  = PTP_PROFILE_INV.get(cur_ptp.get("profile"), str(cur_ptp.get("profile")))
        cur_network  = PTP_NETWORK_INV.get(cur_ptp.get("network"), str(cur_ptp.get("network")))
        want_profile = PTP_PROFILE_INV[wp] if wp is not None else None
        want_network = PTP_NETWORK_INV[wn] if wn is not None else None

        ptp_field_diffs = (
            (wp  is not None and cur_ptp.get("profile") != wp) or
            (wd  is not None and cur_ptp.get("domain")  != wd) or
            (wn  is not None and cur_ptp.get("network") != wn) or
            (wa  is not None and cur_ptp.get("logAnnounceInterval")    != wa) or
            (ws  is not None and cur_ptp.get("logSyncInterval")        != ws) or
            (wdl is not None and cur_ptp.get("logMinDelayReqInterval") != wdl)
        )

        eff_wp  = wp  if wp  is not None else cur_ptp.get("profile", 0)
        eff_wd  = wd  if wd  is not None else cur_ptp.get("domain",  0)
        eff_wn  = wn  if wn  is not None else cur_ptp.get("network", 0)
        eff_wa  = wa  if wa  is not None else cur_ptp.get("logAnnounceInterval",    1)
        eff_ws  = ws  if ws  is not None else cur_ptp.get("logSyncInterval",        1)
        eff_wdl = wdl if wdl is not None else cur_ptp.get("logMinDelayReqInterval", 0)

        def ptp_apply_fn(ewp=eff_wp, ewd=eff_wd, ewn=eff_wn,
                         ewa=eff_wa, ews=eff_ws, ewdl=eff_wdl):
            payload = build_ptp_1588v2(ewd, ewn, ewa, ews, ewdl) if ewp == 0 \
                      else build_ptp_8021as(ewd)
            send_command(ip, port, CMD_SET_PTP_CONFIG, payload, timeout)

        # 리포트 행 (표시 전용 — PTP는 한 커맨드로 묶어 처리)
        row("PTP Profile", cur_profile, want_profile, None)
        row("PTP Domain",  cur_ptp.get("domain"), wd, None)
        row("PTP Network", cur_network, want_network, None)
        if cur_ptp.get("profile") == 0:
            row("logAnnounceInterval",    cur_ptp.get("logAnnounceInterval"),    wa,  None)
            row("logSyncInterval",        cur_ptp.get("logSyncInterval"),        ws,  None)
            row("logMinDelayReqInterval", cur_ptp.get("logMinDelayReqInterval"), wdl, None)
        row("PTP Status", "Enabled" if cur_ptp.get("status") else "Disabled")

        if ptp_field_diffs:
            if ptp_applicable:
                # PTP 모드일 때만 실제 변경 적용
                changes = []
                if wp  is not None and cur_ptp.get("profile") != wp:
                    changes.append(f"profile: {cur_profile} → {want_profile}")
                if wd  is not None and cur_ptp.get("domain")  != wd:
                    changes.append(f"domain: {cur_ptp.get('domain')} → {wd}")
                if wn  is not None and cur_ptp.get("network") != wn:
                    changes.append(f"network: {cur_network} → {want_network}")
                if wa  is not None and cur_ptp.get("logAnnounceInterval")    != wa:
                    changes.append(f"logAnnounce: {cur_ptp.get('logAnnounceInterval')} → {wa}")
                if ws  is not None and cur_ptp.get("logSyncInterval")        != ws:
                    changes.append(f"logSync: {cur_ptp.get('logSyncInterval')} → {ws}")
                if wdl is not None and cur_ptp.get("logMinDelayReqInterval") != wdl:
                    changes.append(f"logMinDelay: {cur_ptp.get('logMinDelayReqInterval')} → {wdl}")
                result["diffs"].append(Diff("PTP Config", "(current)",
                                            ", ".join(changes), ptp_apply_fn))
            else:
                # GPS 모드: PTP 설정 변경 불가 → 경고
                warn("Clock Source가 GPS입니다. PTP Config 변경은 PTP 모드에서만 가능합니다")

    else:
        result["report"].append({"label": "PTP Config", "current": "조회 실패",
                                  "expected": None, "mismatch": False})

    # ── [ Status ] ────────────────────────────────────────────────────────
    section("[ Status ]")

    if cur_st:
        uptime = cur_st["system_uptime"]
        h, r = divmod(uptime, 3600); m, sec = divmod(r, 60)
        row("System Uptime",        f"{uptime}s  ({h}h {m}m {sec}s)")
        row("Motor Speed (RPM)",    cur_st["motor_speed_rpm"])
        row("Startup Times",        cur_st["startup_times"])
        row("Total Operation (s)",  cur_st["total_operation_time"])
        row("GPS PPS Lock",         "Locked" if cur_st["gps_pps_lock"] else "Unlocked")
        row("GPS GPRMC Status",     "Locked" if cur_st["gps_gprmc_status"] else "Unlocked")
        row("PTP Clock Status",     cur_st["ptp_clock_status"])

    else:
        result["report"].append({"label": "Status", "current": "조회 실패",
                                  "expected": None, "mismatch": False})

    return result


# ── 출력 ──────────────────────────────────────────────────────────────────────

def print_lidar_header(name, ip, port, index=None, total=None):
    idx = f"[{index}/{total}] " if index and total else ""
    print(f"\n{MAGENTA}{BOLD}{'═'*62}")
    print(f"  {idx}{name}  ({ip}:{port})")
    print(f"{'═'*62}{RESET}")


def print_report(report):
    col = 30
    for item in report:
        if item.get("section"):
            print(f"\n  {CYAN}{BOLD}{item['section']}{RESET}")
            continue
        label = item["label"]
        cur   = str(item["current"])
        exp   = item.get("expected")
        mis   = item.get("mismatch", False)
        if exp is not None:
            if mis:
                print(f"  {label:<{col}} {RED}{cur:<20}{RESET}  기대: {YELLOW}{exp}{RESET}  {RED}✘{RESET}")
            else:
                print(f"  {label:<{col}} {GREEN}{cur:<20}{RESET}  {GREEN}✔{RESET}")
        else:
            print(f"  {label:<{col}} {cur}")


def print_summary(results):
    print(f"\n{CYAN}{BOLD}{'═'*62}")
    print("  전체 결과 요약")
    print(f"{'═'*62}{RESET}")
    for r in results:
        if r.get("error"):
            print(f"  {BOLD}{r['name']}{RESET} ({r['ip']})  {RED}✘ 연결 오류: {r['error']}{RESET}")
        elif r.get("skipped"):
            print(f"  {BOLD}{r['name']}{RESET} ({r['ip']})  {YELLOW}– 취소됨{RESET}")
        elif not r.get("diffs"):
            print(f"  {BOLD}{r['name']}{RESET} ({r['ip']})  {GREEN}✔ 변경 없음{RESET}")
        else:
            ok = r.get("success", 0); fail = r.get("failed", 0)
            st = f"{GREEN}{ok}개 적용{RESET}" + (f" / {RED}{fail}개 실패{RESET}" if fail else "")
            print(f"  {BOLD}{r['name']}{RESET} ({r['ip']})  {st}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Hesai PTC Multi LiDAR Configurator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python hesai_ptc_configurator.py --config config.yaml
  python hesai_ptc_configurator.py --config config.yaml --yes
  python hesai_ptc_configurator.py --config config.yaml --only sensor_front
  python hesai_ptc_configurator.py --generate-sample
        """
    )
    parser.add_argument("--config",          metavar="FILE", help="설정 파일 (.yaml / .json)")
    parser.add_argument("--generate-sample", action="store_true", help="샘플 설정 파일 생성")
    parser.add_argument("--yes",   "-y",     action="store_true", help="변경 확인 없이 자동 적용")
    parser.add_argument("--only",            nargs="+", metavar="NAME", help="특정 LiDAR만 처리")
    args = parser.parse_args()

    if args.generate_sample:
        generate_sample_config(); return

    if not args.config:
        parser.print_help(); sys.exit(1)

    cfg_file   = load_config(args.config)
    defaults   = cfg_file.get("defaults", {})
    lidar_list = cfg_file.get("lidars", [])

    if not lidar_list:
        print("[ERROR] 설정 파일에 'lidars' 항목이 없습니다."); sys.exit(1)

    if args.only:
        lidar_list = [l for l in lidar_list if l.get("name") in args.only]
        if not lidar_list:
            print(f"[ERROR] --only 에 해당하는 LiDAR 없음: {args.only}"); sys.exit(1)

    try:
        resolved = [resolve_lidar(l, defaults) for l in lidar_list]
    except ValueError as e:
        print(f"[ERROR] {e}"); sys.exit(1)

    print(f"\n{BOLD}LiDAR {len(resolved)}대 순차 처리 시작{RESET}")

    results = []
    for i, lc in enumerate(resolved, 1):
        print_lidar_header(lc["name"], lc["ip"], lc["port"], i, len(resolved))
        print(f"  {DIM}상태 조회 중...{RESET}", flush=True)

        res = process_lidar(lc)
        results.append(res)

        if res.get("error"):
            print(f"  {RED}✘ {res['error']}{RESET}")
            continue

        n_diff = len([d for d in res["diffs"] if d.apply_fn])
        n_warn = len([d for d in res["diffs"] if not d.apply_fn])
        status = f"  {GREEN}완료{RESET}"
        if n_diff: status += f"  {YELLOW}{n_diff}개 변경 필요{RESET}"
        if n_warn: status += f"  {RED}{n_warn}개 수동 필요{RESET}"
        print(status)

        print_report(res["report"])

        # ── 리포트 확인 후 다음으로 ──────────────────────────────────────
        if len(resolved) > 1:
            remaining = len(resolved) - i
            if remaining > 0:
                print(f"\n  {DIM}(다음 LiDAR로 넘어가려면 Enter, 전체 중단은 q): {RESET}", end="")
                if input().strip().lower() == "q":
                    print(f"  {YELLOW}전체 작업을 중단합니다.{RESET}")
                    for r in results:
                        if not r.get("error") and not r.get("success"):
                            r["skipped"] = True
                    print_summary(results)
                    return

        if not res["diffs"]:
            continue

        # 경고 항목 표시
        warns = [d for d in res["diffs"] if not d.apply_fn]
        if warns:
            print(f"\n  {RED}{BOLD}수동 변경 필요 [{lc['name']}  {lc['ip']}]:{RESET}")
            for d in warns:
                print(f"    ⚠  {d.current}")

        # 적용 가능 항목 표시 및 확인
        appliables = [d for d in res["diffs"] if d.apply_fn]
        if not appliables:
            continue

        print(f"\n  {YELLOW}{BOLD}변경이 필요한 항목 [{lc['name']}  {lc['ip']}]:{RESET}")
        for d in appliables:
            print(f"    • {d.label:<28} {RED}{d.current}{RESET} → {GREEN}{d.desired}{RESET}")

        if args.yes:
            do_apply = True
        else:
            print(f"\n  {BOLD}위 {len(appliables)}개 항목을 변경하시겠습니까? (y/N/q): {RESET}", end="")
            ans = input().strip().lower()
            if ans == "q":
                print(f"  {YELLOW}전체 작업을 중단합니다.{RESET}")
                for r in results:
                    if not r.get("error") and not r.get("success"):
                        r["skipped"] = True
                break
            do_apply = (ans == "y")

        if not do_apply:
            print(f"  {YELLOW}건너뜁니다.{RESET}")
            res["skipped"] = True
            continue

        res["success"] = res["failed"] = 0
        for d in appliables:
            try:
                d.apply_fn()
                print(f"  {GREEN}✔{RESET} {d.label} 적용 완료")
                res["success"] += 1
            except Exception as e:
                print(f"  {RED}✘{RESET} {d.label} 적용 실패: {e}")
                import traceback; traceback.print_exc()
                res["failed"] += 1

    print_summary(results)
    if any(r.get("success", 0) > 0 for r in results):
        print(f"\n{YELLOW}※ 일부 변경 사항은 LiDAR 재시작 후 반영될 수 있습니다.{RESET}")


if __name__ == "__main__":
    main()
