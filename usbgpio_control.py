#!/usr/bin/env python3
import serial
import time
import sys

# 지원 가능한 보드레이트 목록
SUPPORTED_BAUDS = [115200, 9600]
DEFAULT_BAUD = 115200

# 커맨드 간 대기 시간 (초)
DELAY = 0.5

# 모드별 명령어 시퀀스
POWEROFF_COMMANDS   = ['gpio iomask ff', 'gpio iodir 00', 'gpio writeall 00']
FWDNMODE_COMMANDS   = ['gpio iomask 8f', 'gpio writeall 80']
SNOR_COMMANDS       = ['gpio iomask 8f', 'gpio writeall 81']
SNOR_EMMC_COMMANDS  = ['gpio iomask 8f', 'gpio writeall 82']
EMMC_COMMANDS       = ['gpio iomask 8f', 'gpio writeall 85']
SNOR_UFS_COMMANDS   = ['gpio iomask 8f', 'gpio writeall 8a']
UFS_COMMANDS        = ['gpio iomask 8f', 'gpio writeall 8d']
USB3FWDN_COMMANDS   = ['gpio iomask 8f', 'gpio writeall 88']
STR_MODE_COMMANDS   = ['gpio iomask c0', 'gpio writeall c0',
                       'gpio writeall 40', 'gpio writeall c0',
                       'gpio writeall 80']

def send_and_print(ser, cmd):
    ser.write((cmd + '\r').encode())
    time.sleep(DELAY)
    resp = ser.read_all().decode(errors='ignore').strip()
    print(f"> {cmd}")
    print(f"< {resp or '(no response)'}\n")

def run_sequence_silent(ser, seq, mode_name):
    for cmd in seq:
        ser.write((cmd + '\r').encode())
        time.sleep(DELAY)
        ser.read_all()
    print(f"[OK] {mode_name} sequence completed\n")

def get_baud():
    prompt = f"Speed (Select: {', '.join(map(str, SUPPORTED_BAUDS))} │ Default {DEFAULT_BAUD}): "
    s = input(prompt).strip()
    if not s:
        return DEFAULT_BAUD
    try:
        b = int(s)
    except ValueError:
        print(f"[WARNING] Not Number. Default {DEFAULT_BAUD} use\n")
        return DEFAULT_BAUD
    if b not in SUPPORTED_BAUDS:
        print(f"[WARNING] Not support speed. Default {DEFAULT_BAUD} use\n")
        return DEFAULT_BAUD
    return b

def main():
    print("=== Numato USB GPIO CLI Control ===")
    port = input("Serial (ex: COM3, /dev/ttyUSB0): ").strip()
    if not port:
        print("input port.")
        sys.exit(1)
    baud = get_baud()

    try:
        ser = serial.Serial(port, baudrate=baud, timeout=1, write_timeout=1)
    except Exception as e:
        print(f"[ERROR] Fail open port: {e}")
        sys.exit(1)

    print(f"[OK] {port}@{baud} Connected\n")

    menu = """
==== Select Mode ====
 1) POWER OFF
 2) FWDN mode
 3) SNOR mode
 4) SNOR + eMMC mode
 5) eMMC mode
 6) SNOR + UFS mode
 7) UFS mode
 8) USB3.0 FWDN mode
 9) STR mode
===================
(exit: 0)
"""

    try:
        while True:
            print(menu)
            choice = input("Select> ").strip()
            if choice == "0":
                print("\nExit.")
                sys.exit(0)
            elif choice == "1":
                print("[MODE] POWER OFF")
                run_sequence_silent(ser, POWEROFF_COMMANDS, "POWER OFF")
            elif choice == "2":
                print("[MODE] FWDN")
                run_sequence_silent(ser, FWDNMODE_COMMANDS, "FWDN")
            elif choice == "3":
                print("[MODE] SNOR")
                run_sequence_silent(ser, SNOR_COMMANDS, "SNOR")
            elif choice == "4":
                print("[MODE] SNOR + eMMC")
                run_sequence_silent(ser, SNOR_EMMC_COMMANDS, "SNOR + eMMC")
            elif choice == "5":
                print("[MODE] eMMC")
                run_sequence_silent(ser, EMMC_COMMANDS, "eMMC")
            elif choice == "6":
                print("[MODE] SNOR + UFS")
                run_sequence_silent(ser, SNOR_UFS_COMMANDS, "SNOR + UFS")
            elif choice == "7":
                print("[MODE] UFS")
                run_sequence_silent(ser, UFS_COMMANDS, "UFS")
            elif choice == "8":
                print("[MODE] USB3.0 FWDN")
                run_sequence_silent(ser, USB3FWDN_COMMANDS, "USB3.0 FWDN")
            elif choice == "9":
                print("[MODE] STR")
                run_sequence_silent(ser, STR_MODE_COMMANDS, "STR MODE")
            else:
                print("Enter a correct number.\n")
    except KeyboardInterrupt:
        print("\nExit.")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
