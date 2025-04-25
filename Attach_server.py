import subprocess
import time
import re
import sys
import select

def list_exported_busids(server_ip):
    subprocess.run(["modprobe", "vhci-hcd"])
    try:
        result = subprocess.run(
            ["usbip", "list", "-r", server_ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            check=True
        )
    except subprocess.CalledProcessError:
        return []

    output = result.stdout
    busids = re.findall(r"^\s*(\d+-[\d\.]+):", output, re.MULTILINE)
    return busids

def attach_all(server_ip, busids):
    attached = []
    for busid in busids:
        try:
            subprocess.run(["usbip", "attach", "-r", server_ip, "-b", busid], check=True)
            print(f"[ATTACH] Success: {busid}")
            attached.append(busid)
        except subprocess.CalledProcessError:
            print(f"[ATTACH] Failed: {busid}")
    return attached

def detach_all_ports():
    try:
        result = subprocess.run(["usbip", "port"], stdout=subprocess.PIPE, universal_newlines=True)
        ports = re.findall(r"Port (\d+): <Port in Use>", result.stdout)
        for port in ports:
            subprocess.run(["usbip", "detach", "-p", port])
            print(f"[DETACH] Port {port} detached")
    except Exception as e:
        print(f"[ERROR] Failed to detach: {e}")

def get_current_attached_busids():
    result = subprocess.run(["usbip", "port"], stdout=subprocess.PIPE, universal_newlines=True)
    return set(re.findall(r"usbip://.+?/([\d\-\.]+)", result.stdout))

def watchdog_loop(server_ip, initial_busids):
    print(f"[WATCHDOG] Monitoring devices: {initial_busids}")
    print("Type 'd' then Enter to detach and exit.")
    known_busids = set(initial_busids)
    retry_counts = {busid: 0 for busid in known_busids}
    MAX_RETRIES = 3

    try:
        while True:
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                cmd = sys.stdin.readline().strip()
                if cmd.lower() == "d":
                    detach_all_ports()
                    print("[WATCHDOG] Exit by user.")
                    break

            current_attached = get_current_attached_busids()

            for busid in known_busids.copy():
                if busid not in current_attached:
                    if retry_counts[busid] >= MAX_RETRIES:
                        print(f"[WATCHDOG] Giving up on {busid}: exceeded retry limit ({MAX_RETRIES})")
                        known_busids.remove(busid)
                        continue

                    print(f"[WATCHDOG] Attempting re-attach for {busid} (attempt {retry_counts[busid]+1})")
                    try:
                        result = subprocess.run(
                            ["usbip", "attach", "-r", server_ip, "-b", busid],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, check=True
                        )
                        print(f"[WATCHDOG] Re-attached: {busid}")
                        retry_counts[busid] = 0
                    except subprocess.CalledProcessError as e:
                        retry_counts[busid] += 1
                        if "Device busy" in e.stderr:
                            print(f"[WATCHDOG] Skipped busy device: {busid}")
                        else:
                            print(f"[WATCHDOG] Failed to re-attach {busid}: {e.stderr.strip()}")

            result = subprocess.run(["usbip", "list", "-r", server_ip],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    universal_newlines=True)
            available = re.findall(r"^\s*(\d+-[\d\.]+):", result.stdout, re.MULTILINE)

            for busid in available:
                if busid not in known_busids:
                    print(f"[WATCHDOG] New bound device detected: {busid}")
                    try:
                        subprocess.run(["usbip", "attach", "-r", server_ip, "-b", busid], check=True)
                        known_busids.add(busid)
                        retry_counts[busid] = 0
                        print(f"[WATCHDOG] Attached new device: {busid}")
                    except subprocess.CalledProcessError as e:
                        print(f"[WATCHDOG] Failed to attach new device {busid}: {e}")

            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[WATCHDOG] Interrupted. Detaching and exiting...")
        detach_all_ports()

def main():
    print("Select action: (a)ttach / (q)uit")
    choice = input("> ").strip().lower()

    if choice == "a":
        server_ip = input("Enter server IP: ").strip()
        exportable = list_exported_busids(server_ip)
        if not exportable:
            print("[INFO] No devices found.")
            sys.exit(0)

        attached = attach_all(server_ip, exportable)
        if attached:
            print("[INFO] Attach complete. Starting watchdog.")
            print("[INFO] Type 'd' and press Enter at any time to detach and exit.")
            watchdog_loop(server_ip, attached)

    elif choice == "q":
        print("Bye.")
        detach_all_ports()
        sys.exit(0)

if __name__ == "__main__":
    main()
