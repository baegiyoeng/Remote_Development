#!/usr/bin/env python3
import subprocess, time, re, sys, threading, os, select
import serial

# ————— Configuration —————
DEFAULT_BAUD   = 115200
GPIO_PORT      = "/dev/ttyACM0"
DELAY          = 0.1
USBIP_LOG_MAX  = 10   # keep last N log lines
BOX_WIDTH      = 60   # interface box width

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

# ————— USB/IP Logging —————
usbip_logs = []

def usbip_log(msg: str):
    usbip_logs.append(msg)
    if len(usbip_logs) > USBIP_LOG_MAX:
        usbip_logs.pop(0)

def clear_screen():
    os.system('cls' if os.name=='nt' else 'clear')

def render_menu():
    clear_screen()
    # Top box: GPIO Control Menu
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

    # Bottom box: USB/IP Log
    print("+" + "-"*(BOX_WIDTH-2) + "+")
    log_title = " USB/IP Log "
    print("|" + log_title.center(BOX_WIDTH-2) + "|")
    print("+" + "-"*(BOX_WIDTH-2) + "+")
    for line in usbip_logs:
        print("| " + line[:BOX_WIDTH-4].ljust(BOX_WIDTH-4) + " |")
    for _ in range(USBIP_LOG_MAX - len(usbip_logs)):
        print("| " + " "*(BOX_WIDTH-4) + " |")
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
            err = e.stderr.lower()
            if "import device" in err:
                usbip_log("이 보드는 다른 사용자가 점유하고 있습니다")
                sys.exit(1)
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

def watchdog_loop(server_ip, initial_busids):
    usbip_log(f"[WATCHDOG] Monitoring: {initial_busids}")
    known = set(initial_busids)
    retries = {b: 0 for b in known}
    MAX_RETRY = 3
    while True:
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
                    usbip_log(f"[WATCHDOG] Failed attach new {b}: {e.stderr.strip()}")
        time.sleep(DELAY)

# ————— GPIO Control —————
def run_mode(ser, seq, name):
    for cmd in seq:
        ser.write((cmd+"\r").encode())
        time.sleep(DELAY)
        ser.read_all()
    usbip_log(f"[OK] {name} done")

def gpio_flow():
    try:
        ser = serial.Serial(GPIO_PORT, baudrate=DEFAULT_BAUD, timeout=1, write_timeout=1)
        usbip_log(f"[GPIO] {GPIO_PORT}@{DEFAULT_BAUD} connected")
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

# ————— Main Flow —————
if __name__ == "__main__":
    while True:
        server_ip = input("Server IP for usbip attach: ").strip()
        if server_ip:
            break
        print("Server IP is required.")

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
            break
        time.sleep(DELAY)
    else:
        usbip_log("usbip server의 연결을 실패했습니다.")
        render_menu()
        sys.exit(1)

    # Start watchdog thread
    threading.Thread(target=watchdog_loop, args=(server_ip,attached), daemon=True).start()

    # Initial render before GPIO menu
    render_menu()

    time.sleep(3)
    # GPIO menu loop
    gpio_flow()

    # Detach & exit
    detach_all_ports()
    usbip_log("Detached all & exiting")
    render_menu()
    print("All done. Goodbye!")
