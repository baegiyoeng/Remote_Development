#!/usr/bin/env python3
import subprocess
import time
import re
import sys
import threading
import os
import select
import serial
import glob
import socket
import requests
import signal

# ————— Configuration —————
DEFAULT_BAUD   = 115200
#GPIO_PORT      = "/dev/ttyACM0"
DELAY          = 0.1
USBIP_LOG_MAX  = 10   # keep last N log lines
BOX_WIDTH      = 60   # interface box width
LOG_FILE       = "Remote_control.txt"
API_URL       = "http://10.10.77.137:5001/api/data"

# ————— Command Sequences —————
POWEROFF    = ['gpio iomask ff','gpio iodir 00','gpio writeall 00']
FWDN        = ['gpio iomask 8f','gpio writeall 80']
SNOR        = ['gpio iomask 8f','gpio writeall 81']
SNOR_EMMC   = ['gpio iomask 8f','gpio writeall 82']
EMMC        = ['gpio iomask 8f','gpio writeall 85']
SNOR_UFS    = ['gpio iomask 8f','gpio writeall 8a']
UFS         = ['gpio iomask 8f','gpio writeall 8d']
USB3FWDN    = ['gpio iomask 8f','gpio writeall 88']
STR_MODE    = ['gpio iomask c0','gpio writeall c0',
               'gpio writeall 40','gpio writeall c0','gpio writeall 80']

SERVER_IP = None   # 전역으로 선택된 서버 IP 저장

def get_attached_devices():
    """
    현재 `usbip port` 로 붙어 있는 디바이스의 BusID 리스트를 리턴.
    """
    try:
        out = subprocess.run(
            ["usbip","port"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            check=True
        ).stdout
        return re.findall(r"usbip://[^/]+/([\d\-\.]+)", out)
    except:
        return []

def get_serial_ports():
    """
    /dev/ttyACM* 와 /dev/ttyUSB* 중 실제 존재하는 포트를 리스트로 반환
    """
    ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    return sorted(ports)

# ————— Server List —————
def select_server(servers):
    # 1) API에서 할당 현황 불러오기
    try:
        r = requests.get(API_URL, timeout=2)
        r.raise_for_status()
        allocs = r.json().get("data", [])
    except:
        allocs = []

    # 2) 각 서버별 상태 판정
    statuses = []
    for ip in servers:
        # A) exportable bus ID 목록 조회
        try:
            out = subprocess.run(
                ["usbip","list","-r",ip],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                timeout=1,
                check=True
            ).stdout
            busids = re.findall(r"^\s*(\d+-[\d\.]+):", out, re.MULTILINE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # 실패 또는 타임아웃 시 장치 없음으로 간주
            busids = []

        has_devices = bool(busids)
        # B) API 점유자 확인
        holders = [r["source_ip"] for r in allocs if r["value"] == ip]

        # C) free 여부
        free = has_devices and not holders

        # 저장: (free, holders리스트, has_devices)
        statuses.append((free, holders, has_devices))

    # 3) 목록 출력
    print("Available USB/IP servers:")
    for idx, ip in enumerate(servers, 1):
        free, holders, has_dev = statuses[idx-1]
        if free:
            mark, info = "[O]", ""
        else:
            mark = "[X]"
            if holders:
                info = f" ← in use by {holders[0]}"
            elif not has_dev:
                info = " ← no exportable devices"
            else:
                info = ""
        print(f"  {idx}) {ip} {mark}{info}")
    print("  0) Exit")

    # 4) 선택 루프
    while True:
        choice = input(f"Select server [1-{len(servers)}] or 0 to exit: ").strip()
        if choice == "0":
            print("All done. Goodbye!")
            sys.exit(0)
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(servers):
                free, holders, has_dev = statuses[n-1]
                if free:
                    return servers[n-1]
                # 선택 불가 사유만 다시 안내
                if holders:
                    print(f"{servers[n-1]} 서버는 이미 {holders[0]} 클라이언트가 사용 중입니다.")
                elif not has_dev:
                    print(f"{servers[n-1]} 서버에는 연결 가능한 장치가 없습니다.")
                else:
                    print(f"{servers[n-1]} 서버는 연결 불가 상태입니다.")
                continue
        print(f"Invalid choice '{choice}'. Enter 0 or 1~{len(servers)}.")

# ————— USB/IP Logging —————
usbip_logs = []

with open(LOG_FILE, 'w', encoding='utf-8') as _:
    pass

def usbip_log(msg: str):
    """멀티라인 메시지도 각 줄마다 타임스탬프를 붙여서 파일에 저장."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        for line in msg.splitlines():
            f.write(f"{timestamp} {line}\n")

def clear_screen():
    os.system('cls' if os.name=='nt' else 'clear')

def render_menu():
    clear_screen()
    # ─── 상단: 서버 IP & 실제 시리얼 포트 ────────────────────────
    print(f"Server IP: {SERVER_IP or '<none>'}")
    serials = get_serial_ports()
    if serials:
        print("Serial ports: " + ", ".join(serials))
    else:
        print("Serial ports: None")
    print()  # 빈 줄
    # Top box: GPIO Control Menu only
    print("+" + "-"*(BOX_WIDTH-2) + "+")
    title = " GPIO Control Menu "
    print("|" + title.center(BOX_WIDTH-2) + "|")
    print("+" + "-"*(BOX_WIDTH-2) + "+")
    for idx,name in [
        ("1","Power Off"),("2","FWDN Mode"),("3","SNOR Mode"),
        ("4","SNOR+eMMC"),("5","eMMC Mode"),("6","SNOR+UFS"),
        ("7","UFS Mode"),("8","USB3.0 FWDN"),("9","STR Mode"),
        ("0","Exit & detach")
    ]:
        line = f" {idx}) {name}"
        print("|" + line.ljust(BOX_WIDTH-2) + "|")
    print("+" + "-"*(BOX_WIDTH-2) + "+")

# ————— USB/IP Functions —————
def list_exported_busids(server_ip):
    subprocess.run(["modprobe","vhci-hcd"], stderr=subprocess.DEVNULL)
    time.sleep(DELAY)
    try:
        out = subprocess.run([
            "usbip","list","-r",server_ip
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        return []
    return re.findall(r"^\s*(\d+-[\d\.]+):", out, re.MULTILINE)

def attach_all(server_ip, busids):
    attached = []
    for b in busids:
        try:
            subprocess.run([
                "usbip","attach","-r",server_ip,"-b",b
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True
            )
            usbip_log(f"[ATTACH] Success: {b}")
            attached.append(b)
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "").lower()
            if "import device" in err:
                # 점유된 장치는 건너뛰고 계속 진행
                usbip_log(f"[ATTACH] Skipped busy (already in use): {b}")
                continue
            # 그 외 실패는 상세히 기록
            usbip_log(f"[ATTACH] Failed: {b} ({e.stderr.strip()})")
    return attached

def detach_all_ports():
    try:
        out = subprocess.run([
            "usbip","port"
        ], stdout=subprocess.PIPE,
            universal_newlines=True
        ).stdout
        for p in re.findall(r"Port (\d+): <Port in Use>", out):
            subprocess.run([
                "usbip","detach","-p",p
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            usbip_log(f"[DETACH] Port {p} detached")
    except Exception as e:
        usbip_log(f"[ERROR] Failed detach: {e}")

def watchdog_loop(server_ip, initial_busids):
    usbip_log(f"[WATCHDOG] Monitoring: {initial_busids}")
    known = set(initial_busids)
    retries = {b: 0 for b in known}
    MAX_RETRY = 3
    while True:
        time.sleep(1)
        outp = subprocess.run([
            "usbip","port"
        ], stdout=subprocess.PIPE,
            universal_newlines=True
        ).stdout
        attached_now = set(re.findall(r"usbip://.+?/([\d\-\.]+)", outp))

        for b in list(known):
            if b not in attached_now:
                if retries[b] >= MAX_RETRY:
                    usbip_log(f"[WATCHDOG] Give up on {b}")
                    known.remove(b)
                else:
                    usbip_log(f"[WATCHDOG] Re-attach {b} (#{retries[b]+1})")
                    try:
                        subprocess.run([
                            "usbip","attach","-r",server_ip,"-b",b
                        ], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=True
                        )
                        usbip_log(f"[WATCHDOG] Re-attached {b}")
                        retries[b] = 0
                    except subprocess.CalledProcessError:
                        retries[b] += 1
                        usbip_log(f"[WATCHDOG] Re-attach failed for {b} (err #{retries[b]})")

        exportable = list_exported_busids(server_ip)
        for b in exportable:
            if b not in known:
                usbip_log(f"[WATCHDOG] New exportable detected: {b}")
                try:
                    subprocess.run([
                        "usbip","attach","-r",server_ip,"-b",b
                    ], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, universal_newlines=True, check=True
                    )
                    usbip_log(f"[WATCHDOG] Attached new {b}")
                    known.add(b)
                    retries[b] = 0
                except subprocess.CalledProcessError as e:
                    usbip_log(f"[WATCHDOG] Failed attach new {b}:\n{e.stderr}")
        time.sleep(DELAY)

# ————— GPIO Control —————
def run_mode(ser, seq, name):
    for cmd in seq:
        ser.write((cmd+"\r").encode())
        time.sleep(DELAY)
        ser.read_all()
    usbip_log(f"[OK] {name} done")

def find_acm_port():
    ports = glob.glob("/dev/ttyACM*")
    ports.sort()
    return ports[0] if ports else None

def gpio_flow():
    port = find_acm_port()
    if not port:
        usbip_log("[GPIO ERROR] No ACM port found")
        return
    try:
        ser = serial.Serial(port, baudrate=DEFAULT_BAUD, timeout=1, write_timeout=1)
        usbip_log(f"[GPIO] {port}@{DEFAULT_BAUD} connected")
    except Exception as e:
        usbip_log(f"[GPIO ERROR] {e}")
        return

    while True:
        render_menu()
        try:
            c = input("Select> ").strip()
        except (EOFError, KeyboardInterrupt):
            usbip_log("[GPIO] Input interrupted, continue")
            continue
        if c == "0":
            break
        mapping = {
            "1": (POWEROFF, "Power Off"),
            "2": (FWDN, "FWDN Mode"),
            "3": (SNOR, "SNOR Mode"),
            "4": (SNOR_EMMC, "SNOR+eMMC"),
            "5": (EMMC, "eMMC Mode"),
            "6": (SNOR_UFS, "SNOR+UFS"),
            "7": (UFS, "UFS Mode"),
            "8": (USB3FWDN, "USB3.0 FWDN"),
            "9": (STR_MODE, "STR Mode")
        }
        if c in mapping:
            seq,name = mapping[c]
            usbip_log(f"[MODE] {name}")
            run_mode(ser, seq, name)
        else:
            usbip_log("[GPIO] Enter 0-9")

    ser.close()
    usbip_log("[GPIO] Port closed")

def report_to_api(server_ip, api_url=API_URL):
    """
    서버 IP와 내 IP를 API 서버에 POST로 보고합니다.
    """
    client_ip = socket.gethostbyname(socket.gethostname())
    payload = {
        "source_ip": client_ip,
        "value":     server_ip,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    try:
        r = requests.post(api_url, json=payload, timeout=2)
        r.raise_for_status()
        usbip_log(f"[REPORT] OK → {payload}")
    except Exception as e:
        usbip_log(f"[REPORT] FAIL → {e}")

# ————— Signal Handler —————
def handle_sigint(signum, frame):
    usbip_log("[INFO] SIGINT received, cleaning up...")
    detach_all_ports()
    try:
        if not get_serial_ports():
            detach_all_ports()
    except Exception as e:
        usbip_log(f"[ERROR] Failed to detach: {e}")

    client_ip = socket.gethostbyname(socket.gethostname())
    try:
        d = requests.delete(f"{API_URL}/{client_ip}", timeout=2)
        d.raise_for_status()
        usbip_log(f"[REPORT] DELETE OK → {client_ip}")
    except Exception as e:
        usbip_log(f"[REPORT] DELETE FAIL → {e}")
    print("All done. Goodbye!")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sigint)

# ————— Main Flow —————
if __name__ == "__main__":
    servers = [
        "tcremote.telechips.com",
        "10.10.27.132"
    ]
    # 2) 메뉴로 선택
    server_ip = select_server(servers)
    print(f"→ You selected: {server_ip}")

    SERVER_IP = server_ip

    exportable = list_exported_busids(server_ip)
    if not exportable:
        print("[INFO] No exportable USB devices; exiting.")
        sys.exit(0)

    attached = []
    for i in range(1,6):
        usbip_log(f"[INFO] Attach attempt {i}/5")
        attached = attach_all(server_ip, exportable)
        if attached:
            usbip_log("[INFO] Attach complete. Entering GPIO control.")
            # → 여기서 API에 보고
            report_to_api(server_ip)
            break
        time.sleep(DELAY)
    else:
        usbip_log("usbip server의 연결을 실패했습니다.")
        render_menu()
        sys.exit(1)

    time.sleep(2)
    # Start watchdog thread
    threading.Thread(target=watchdog_loop, args=(server_ip,attached), daemon=True).start()

    # Initial render before GPIO menu
    render_menu()

    time.sleep(1)
    # GPIO menu loop
    gpio_flow()

    # Detach & exit
    detach_all_ports()
    usbip_log("Detached all & exiting")
    # API 서버에서도 내 기록 삭제
    client_ip = socket.gethostbyname(socket.gethostname())
    try:
        d = requests.delete(f"{API_URL}/{client_ip}", timeout=2)
        d.raise_for_status()
        usbip_log(f"[REPORT] DELETE OK → {client_ip}")
    except Exception as e:
        usbip_log(f"[REPORT] DELETE FAIL → {e}")
    render_menu()

    # serial port가 남아 있는지 한번 더 점검
    if not get_serial_ports():
        detach_all_ports()
    print("All done. Goodbye!")
