#!/usr/bin/env python3
import subprocess
import time
import re
import sys
import threading
import os
import socket
import signal
import serial
import requests

# ————— Configuration —————
DEFAULT_BAUD = 115200
DELAY        = 0.1
LOG_FILE     = "Remote_control.txt"
API_URL      = "http://10.10.77.137:5001/api/data"

# ON/OFF sequences
POWEROFF  = ['gpio iomask ff', 'gpio iodir 00', 'gpio writeall 00']
SNOR_EMMC = ['gpio iomask 8f', 'gpio writeall 82']

# ————— USB/IP Logging —————
def usbip_log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{ts} {msg}\n")

# ————— USB/IP Helpers —————
def list_exported_busids(server_ip):
    subprocess.run(["modprobe","vhci-hcd"], stderr=subprocess.DEVNULL)
    time.sleep(DELAY)
    try:
        out = subprocess.run(
            ["usbip","list","-r",server_ip],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=True
        ).stdout
    except subprocess.CalledProcessError:
        return []
    return re.findall(r"^\s*(\d+-[\d\.]+):", out, re.MULTILINE)

def attach_all(server_ip, busids):
    attached = []
    for b in busids:
        try:
            subprocess.run(
                ["usbip","attach","-r",server_ip,"-b",b],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True
            )
            usbip_log(f"[ATTACH] Success: {b}")
            attached.append(b)
        except subprocess.CalledProcessError:
            usbip_log(f"[ATTACH] Failed: {b}")
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

# ————— API Reporting —————
def report_to_api(server_ip):
    client_ip = socket.gethostbyname(socket.gethostname())
    payload = {
        "source_ip": client_ip,
        "value":     server_ip,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    try:
        requests.post(API_URL, json=payload, timeout=2).raise_for_status()
        usbip_log(f"[REPORT] POST OK → {payload}")
    except Exception as e:
        usbip_log(f"[REPORT] POST FAIL → {e}")

def delete_from_api():
    client_ip = socket.gethostbyname(socket.gethostname())
    try:
        d = requests.delete(f"{API_URL}/{client_ip}", timeout=2)
        d.raise_for_status()
        usbip_log(f"[REPORT] DELETE OK → {client_ip}")
    except Exception as e:
        usbip_log(f"[REPORT] DELETE FAIL → {e}")

# SIGINT 처리: detach + API 삭제
def on_sigint(signum, frame):
    print("\nInterrupted, cleaning up...")
    detach_all_ports()
    delete_from_api()
    sys.exit(0)
signal.signal(signal.SIGINT, on_sigint)

# ————— Server 선택 —————
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

# ————— GPIO 시퀀스 실행 —————
def run_sequence(ser, seq):
    for cmd in seq:
        ser.write((cmd + '\r').encode())
        time.sleep(DELAY)
        ser.read_all()

# ————— Main Flow —————
if __name__ == "__main__":
    # 1) 서버 선택 & USB/IP attach
    servers   = ["tcremote.telechips.com", "10.10.27.132"]
    server_ip = select_server(servers)
    print(f"→ Selected server: {server_ip}")
    usbip_log(f"[INFO] Attaching to {server_ip}")

    busids = list_exported_busids(server_ip)
    if not busids:
        print("No exportable devices; exiting.")
        sys.exit(1)
    attached = attach_all(server_ip, busids)
    if not attached:
        print("Attach failed; exiting.")
        sys.exit(1)

    # API에 보고
    report_to_api(server_ip)

    # 2) 반복 횟수 입력
    try:
        cycles = int(input("반복할 ON/OFF 사이클 횟수 입력: ").strip())
    except ValueError:
        print("숫자만 입력하세요.")
        delete_from_api()
        sys.exit(1)

    # 3) GPIO 포트 열기
    GPIO_PORT = "/dev/ttyACM0"
    try:
        ser = serial.Serial(GPIO_PORT, DEFAULT_BAUD, timeout=1, write_timeout=1)
    except Exception as e:
        print(f"Cannot open GPIO port {GPIO_PORT}: {e}")
        detach_all_ports()
        delete_from_api()
        sys.exit(1)

    # 4) 초기 1분간 POWER OFF 유지
    print("[INFO] Initial POWER OFF (60s)")
    usbip_log("[MODE] Initial POWER OFF")
    run_sequence(ser, POWEROFF)
    time.sleep(60)

    # 5) ON/OFF 사이클 반복
    for i in range(1, cycles + 1):
        # Power ON 5분
        print(f"[Cycle {i}] POWER ON (300s)")
        usbip_log(f"[MODE] POWER ON (cycle {i})")
        run_sequence(ser, SNOR_EMMC)
        time.sleep(60)

        # Power OFF 10초
        print(f"[Cycle {i}] POWER OFF (10s)")
        usbip_log(f"[MODE] POWER OFF (cycle {i})")
        run_sequence(ser, POWEROFF)
        time.sleep(10)

    # 6) 정리 & 종료
    ser.close()
    print("모든 사이클 완료. Detaching...")
    usbip_log("[INFO] All cycles done; detaching")
    detach_all_ports()
    delete_from_api()
    print("끝났습니다. Goodbye!")