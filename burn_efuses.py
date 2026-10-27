import sys
import serial
import subprocess
import re
import time
import os
import csv
from pathlib import Path
from datetime import datetime
from serial.tools import list_ports

import hmac, hashlib, binascii

COM_PORT = ""                   # leave as "" to auto-detect esp COM port, otherwise manually enter with COMxx format

LOG_MAC_ID_TO_CSV = True

#choose your mode: (choose one, comment the rest)
FLASH_BIT_TO_BURN = "0b001"     #<--recommended 
                                # "Dev" mode- this mode allows you to come back from 
                                # flash encryption one more time when you transition to 0b011.  
                                # Nice for letting makers mod your board if you want to give 
                                # them that freedom (do it ;) )

# FLASH_BIT_TO_BURN = "0b011"   # undo flash encryption: this is a one-time fallback to allow 
                                # a device to be flashed with plain text code once again

# FLASH_BIT_TO_BURN = "0b111"   # "Release" mode- PERMANENT; CANNOT BE UNDONE.  Permantly set to flash encryption.

DEBUG_ABORT = True
ENABLE_INFO = True

PROJECT_DIR = Path(__file__).resolve().parent

FLASH_ENCRYPTION_FILE = PROJECT_DIR / "flash_encryption_key.bin" 

CSV_FILE = PROJECT_DIR / "flash_encrypted_devices.csv" 

FLASH_ENCRYPTION_KEY_SLOT = 0 # BLOCK_KEY0 (BLOCK4) ASSIGNED FOR ENCRYPTION KEY


def info(message):
    if ENABLE_INFO:
        print(f"[INFO] {message}")

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
PURPLE = "\033[35m"
ORANGE = "\033[38;5;208m"
INDIGO = "\033[38;5;54m"
VIOLET = "\033[35m"

BOLD = "\033[1m"
RESET = "\033[0m"

colors = [RED, ORANGE, YELLOW, GREEN, BLUE, INDIGO, VIOLET]
def print_rainbow(text, index=-1, end='\n'):
    output = BOLD
    color_index = 0
    fixed_color = None
    if index != -1:
        fixed_color = colors[index % len(colors)]
    for char in text:
        if fixed_color is not None:
            color = fixed_color
        elif char not in [' ', '\n']:
            color = colors[color_index % len(colors)]
            color_index += 1
        output += f"{color}{char}"
    output += RESET
    print(output, end=end)


def find_esp32_com_port():
    known_usb_ids = {
        (0x303A, 0x1001),  # Espressif USB Serial/JTAG
        (0x10C4, 0xEA60),  # Silicon Labs CP210x
        (0x1A86, 0x7523),  # WCH CH340
        (0x1A86, 0x55D4),  # WCH CH9102
        (0x0403, 0x6001),  # FTDI FT232
    }

    candidates = []
    for port in list_ports.comports():
        description = (port.description or "").lower()
        if (
            (port.vid, port.pid) in known_usb_ids
            or "usb serial/jtag" in description
            or "esp32" in description
            or "cp210" in description
            or "ch340" in description
            or "ch910" in description
        ):
            candidates.append(port.device)

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError(
            "Multiple likely ESP32 serial ports found: "
            + ", ".join(candidates)
            + ". Set COM_PORT explicitly."
        )
    return None


def resolve_com_port():
    global COM_PORT

    if COM_PORT == "":
        detected = find_esp32_com_port()
        if detected is None:
            raise RuntimeError(
                "No likely ESP32 serial port detected. Set COM_PORT manually."
            )
        COM_PORT = detected
        info(f"Automatically detected serial port: {COM_PORT}")
    else:
        if not isinstance(COM_PORT, str) or not re.fullmatch(
            r"COM[1-9]\d*", COM_PORT, flags=re.IGNORECASE
        ):
            raise ValueError(
                f"Invalid COM_PORT {COM_PORT!r}. Use '' for auto-detection "
                'or a value such as "COM21".'
            )
        COM_PORT = COM_PORT.upper()

    available_ports = {port.device.upper() for port in list_ports.comports()}
    if COM_PORT.upper() not in available_ports:
        raise RuntimeError(
            f"{COM_PORT} is not currently listed by Windows. "
            "Check the cable, driver, and selected port."
        )

    return COM_PORT


def load_key(filepath):

    if not os.path.exists(filepath):
        print(f"{RED}ERROR: Key file not found:{RESET} {YELLOW}{filepath}{RESET}")
        return None

    extension = os.path.splitext(filepath)[1].lower()

    try:

        if extension == ".txt":

            with open(filepath, "r") as f:
                key_hex = f.read().strip()

            if len(key_hex) != 64:
                print(f"{RED}ERROR: TXT key must be 64 hex characters{RESET}")
                return None

            try:
                key_bytes = bytes.fromhex(key_hex)
            except ValueError:
                print(f"{RED}ERROR: Invalid hex in TXT key{RESET}")
                return None

            return key_bytes

        elif extension == ".bin":

            with open(filepath, "rb") as f:
                key_bytes = f.read()

            if len(key_bytes) != 32:
                print(f"{RED}ERROR: BIN key must be exactly 32 bytes{RESET}")
                return None

            return key_bytes

        else:
            print(f"{RED}ERROR: Unsupported key file type:{RESET} {extension}")
            return None

    except Exception as e:
        print(f"{RED}ERROR loading key:{RESET} {e}")
        return None


def esp_retrieve_fuse_summary(COM_PORT):

    result = subprocess.run(
        [sys.executable, "-m", "espefuse", "--port", COM_PORT, "summary"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"{RED}Failed to read eFuse summary{RESET}")
        return False  # fail safe
    
    """ FOR PRINTING WHOLE LINE OUT STATEMENT
    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        l = lines[i].strip()
        print(f"{BLUE}Line {i}:{RESET} {l}")
    """

    return result


def confirm_burn(mac):
    print(f"\n{YELLOW}About to burn eFuse for device:{RESET} {BLUE}{mac}{RESET}")
    print(f"{RED}THIS IS PERMANENT AND CANNOT BE UNDONE{RESET}")

    try:
        confirm1 = input("Type 'YES' or 'y' to continue: ")
    except KeyboardInterrupt:
        print(f"\n{RED}User interrupted input. Aborting.{RESET}")
        return False

    if confirm1 == "YES" or confirm1.lower() == "y":
        return True

    return False


def get_esp32_mac(COM_PORT):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "esptool", "--port", COM_PORT, "read-mac"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            print(f"{RED}No ESP32 found on {COM_PORT}, aborting all operations.{RESET}")
            print(result.stderr.strip())
            sys.exit(1)
            return None

        output = result.stdout
        # print(output)

        mac_match = re.search(r"MAC:\s*([0-9A-Fa-f:]+)", output)
        if mac_match:
            mac = mac_match.group(1)
            mac_bytes = bytes(int(b, 16) for b in mac.split(":"))
            print(f"Device MAC: {BLUE}{mac}{RESET}")
            return mac, mac_bytes

        print(f"{RED}Failed to parse MAC{RESET}")
        return None

    except Exception as e:
        print(f"{RED}Error: {e}{RESET}")
        return None


def enter_bootloader(COM_PORT):
    # This only works if the board exposes RTS/DTR for reset/boot
    with serial.Serial(COM_PORT, 115200) as ser:
        ser.setDTR(False)   # DTR low → RESET low
        ser.setRTS(True)    # RTS high → BOOT/IO0 low
        time.sleep(0.1)
        ser.setDTR(True)    # Release RESET
        time.sleep(0.05)
        ser.setRTS(False)   # Release BOOT
        time.sleep(0.1)




def burn_efuse(COM_PORT, flag_FLASH_ENCRYPTION_KEY, SPI_BOOT_CRYPT_CNT_flag):

    try:
        def run_stream(command, description, timeout=60):
            print(f"\n=== {description} ===")
            print(" ".join(command))
            print()

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            try:
                for line in process.stdout:
                    print(line, end="")

                process.wait(timeout=timeout)

            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                print(f"{RED}{description} TIMED OUT{RESET}")
                return False

            if process.returncode != 0:
                print(f"{RED}{description} FAILED{RESET}")
                return False

            print(f"{GREEN}{description} SUCCESS{RESET}")
            return True


        # Burn the flash-encryption key
        if flag_FLASH_ENCRYPTION_KEY:
            key_command = [
                sys.executable,
                "-m", "espefuse",
                "--chip", "esp32c3",
                "--port", COM_PORT,
                "--do-not-confirm",
                "burn-key",
                f"BLOCK_KEY{FLASH_ENCRYPTION_KEY_SLOT}",
                FLASH_ENCRYPTION_FILE,
                "XTS_AES_128_KEY", # THIS DESIGNATES THE SLOT AS USED FOR FLASH DECRYPTION
            ]

            if not run_stream(key_command, "Burn flash-encryption key"): #ensure pass before burning flash enable fuse
                return False

        # Burn the selected SPI_BOOT_CRYPT_CNT bits.
        if SPI_BOOT_CRYPT_CNT_flag:
            counter_command = [
                sys.executable,
                "-m", "espefuse",
                "--chip", "esp32c3",
                "--port", COM_PORT,
                "--do-not-confirm",
                "burn-efuse",
                "SPI_BOOT_CRYPT_CNT",
                str(int(FLASH_BIT_TO_BURN, 2)),
            ]

            if not run_stream(
                counter_command,
                f"Burn SPI_BOOT_CRYPT_CNT to {FLASH_BIT_TO_BURN}",
            ):
                return False

        return True

    except Exception as e:
        print(f"{RED}eFuse provisioning exception: {e}{RESET}")
        return False



def verify_key_purpose(efuse_summary, slot, purpose_name):
    print(f"{BLUE}Verifying key purpose for slot {slot}...{RESET}")

    lines = efuse_summary.stdout.splitlines()

    target = f"KEY_PURPOSE_{slot}"

    for i, line in enumerate(lines):
        l = line.strip()

        if l.startswith(target):
            print(f"{GREEN}Found {target} at line {i}:{RESET}")
            print(f"{PURPLE}{l}{RESET}")

            if "=" in l:
                value = l.split("=")[1].strip()

                print(f"{YELLOW}Parsed purpose: {value}{RESET}")

                if purpose_name in value:
                    print(f"{GREEN}Slot {slot} correctly set for {purpose_name}{RESET}")
                    return True
                else:
                    print(f"{RED}Slot {slot} NOT set for HMAC (found: {value}).  Value should be: {purpose_name}{RESET}")
                    return False

    print(f"{RED}Could not find KEY_PURPOSE_{slot}{RESET}")
    return False


def is_efuse_already_burned(efuse_summary, block_num):

    lines = efuse_summary.stdout.splitlines()
    if block_num == "BLOCK_USR_DATA (BLOCK3)":
        print(f"{RESET}Checking BLOCK_USR_DATA (BLOCK3)...{RESET}")
        block_name = f"BLOCK_USR_DATA (BLOCK3)"
    else:
        print(f"{RESET}Checking eFuse block {block_num}...{RESET}")
        block_name = f"BLOCK_KEY{block_num}"

    for i, line in enumerate(lines):
        if line.strip().startswith(block_name):

            print(f"{GREEN}Found {block_name} at line {i}:{RESET}")
            #print(f"{PURPLE}{line.strip()}{RESET}")

            # Look ahead for the data line
            for j in range(i + 1, min(i + 10, len(lines))):
                l = lines[j].strip()

                # print(f"{BLUE}Scanning line {j}:{RESET} {l}")

                if l.startswith("="):
                    print(f"{GREEN}Found key data line at {j}: {YELLOW}{l}{RESET}")

                    # Extract only hex bytes (ignore R/W)
                    parts = l.split("=")[1].strip().split()
                    hex_bytes = [p for p in parts if len(p) == 2]

                    #print(f"{PURPLE}Parsed hex bytes ({len(hex_bytes)}):{RESET}")
                    #print(f"{YELLOW}{' '.join(hex_bytes)}{RESET}")

                    if len(hex_bytes) != 32:
                        print(f"{RED}Unexpected key length in {block_name}{RESET}")
                        return True  # fail safe

                    if any(b != "00" for b in hex_bytes):
                        print(f"{YELLOW}Notice: {block_name} already programmed!{RESET}")
                        return True
                    else:
                        print(f"{GREEN}{block_name} is EMPTY (all zeros){RESET}")
                        return False

            print(f"{RED}Did not find '=' line for {block_name}{RESET}")
            return True  # fail safe

    print(f"{RED}Could not find {block_name} in eFuse summary{RESET}")
    return True  # fail safe


def is_efuse_flag_burned(efuse_summary, efuse_name):

    print(f"{RESET}Checking eFuse flag {efuse_name}...{RESET}")

    lines = efuse_summary.stdout.splitlines()

    for line in lines:

        stripped = line.strip()

        if not stripped.startswith(efuse_name):
            continue

        print(f"{GREEN}Found {efuse_name}:{RESET}")
        print(f"{YELLOW}{stripped}{RESET}")

        # =====================================================
        # SPECIAL CASE: SPI_BOOT_CRYPT_CNT (bit-parity fuse)
        # =====================================================
        if efuse_name == "SPI_BOOT_CRYPT_CNT":

            lowered = stripped.lower()

            if "disable" in lowered:
                print(f"{GREEN}{efuse_name} is currently DISABLED{RESET}")
                if FLASH_BIT_TO_BURN in ("0b001", "0b111"): return False
                else: return True

            if "enable" in lowered:
                print(f"{YELLOW}{efuse_name} is ENABLED{RESET}")
                if FLASH_BIT_TO_BURN in ("0b001", "0b111"): return True
                else: return False

            # fallback: treat unknown as NOT safe to assume burned
            print(f"{RED}Could not reliably parse SPI_BOOT_CRYPT_CNT state{RESET}")
            return False

        # =====================================================
        # BOOLEAN FUSES
        # =====================================================
        if "= True" in stripped:
            print(f"{YELLOW}{efuse_name} is already burned/enabled{RESET}")
            return True

        if "= False" in stripped:
            print(f"{GREEN}{efuse_name} is not burned{RESET}")
            return False

        # =====================================================
        # NUMERIC FUSES
        # =====================================================
        if "=" in stripped:

            value = stripped.split("=")[1].strip().split()[0]

            try:
                parsed = int(value, 16) if value.startswith("0x") else int(value)

                if parsed != 0:
                    print(f"{YELLOW}{efuse_name} already programmed: {parsed}{RESET}")
                    return True
                else:
                    print(f"{GREEN}{efuse_name} is zero/unset{RESET}")
                    return False

            except:
                pass

        print(f"{RED}Could not parse {efuse_name}{RESET}")
        return True

    print(f"{RED}Could not find {efuse_name} in summary output{RESET}")
    return True


def setFuseFlag(fuse_summary, name, confirmation_message=False):
    if confirmation_message: # reverses statement because it was sloppy quick, don't judge
        if is_efuse_flag_burned(fuse_summary, name):
            print(f"{GREEN}FUSE '{name}' successfully burned!{RESET}")
            return True
        print(f"{RED}ERROR: FUSE '{name}' not burned{RESET}")
        return False
    else:
        if is_efuse_flag_burned(fuse_summary, name):
            print(f"{YELLOW}FUSE '{name}' already burned: Setting flag to prevent overwriting eFuse{RESET}")
            return False
        print(f"{GREEN}FUSE '{name}' not burned: Setting flag for burning{RESET}")
        return True


def log_to_csv(mac): #, master_key_hex, ota_key_hex):
    file_exists = os.path.exists(CSV_FILE)
    ts = datetime.now().isoformat()

    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(["timestamp", "mac", "DEVICE_KEY", "USER_BLOCK"])

        writer.writerow([
            ts,
            mac
        ])

    print(f"{GREEN}Logged to CSV: {CSV_FILE}{RESET}")



def main():
    resolve_com_port() # sets global COM port value

    mac_result = get_esp32_mac(COM_PORT)
    if mac_result is None:
        print(f"mac_result is None{RESET}")
        return False

    mac, mac_bytes = mac_result

    FLASH_ENCRYPTION_KEY = load_key(FLASH_ENCRYPTION_FILE)
    if FLASH_ENCRYPTION_KEY is not None: print(f"{PURPLE}FLASH_ENCRYPTION key loaded{RESET}")
    else: 
        print(f"ERROR: FLASH_ENCRYPTION_KEY is None{RESET}")
        return False

    #FLASH_ENCRYPTION_KEY_hex = binascii.hexlify(FLASH_ENCRYPTION_KEY).decode()
    #info(FLASH_ENCRYPTION_KEY)
    #info(FLASH_ENCRYPTION_KEY_hex)


    if len(FLASH_ENCRYPTION_KEY) != 32:
        raise ValueError("FLASH_ENCRYPTION_KEY must be 32 bytes")

    fuse_summary = esp_retrieve_fuse_summary(COM_PORT)
    if not fuse_summary:
        print(f"{RED}Failed to retrieve eFuse summary, aborting.{RESET}")
        return False

    
    flag_FLASH_ENCRYPTION_KEY = True
    flash_encryption_slot_burned = is_efuse_already_burned(fuse_summary, FLASH_ENCRYPTION_KEY_SLOT)
    if flash_encryption_slot_burned:
        print(f"{YELLOW}slot0 (flash_encryption) already burned: Setting flag to prevent overwriting eFuse{RESET}")
        flag_FLASH_ENCRYPTION_KEY = False
        if not verify_key_purpose(fuse_summary, FLASH_ENCRYPTION_KEY_SLOT, "XTS_AES_128_KEY"):
            print(f"{RED}DESIGNATED SLOT FOR ENCRYPTION ALREADY HAS A DIFFERENT TYPE OF KEY PLACED IN SLOT: {FLASH_ENCRYPTION_KEY_SLOT}{RESET}") 
        else: 
            print(f"{GREEN}XTS_AES_128_KEY has already been assigned in slot: {FLASH_ENCRYPTION_KEY_SLOT}{RESET}") 


    SPI_BOOT_CRYPT_CNT_flag = setFuseFlag(fuse_summary, "SPI_BOOT_CRYPT_CNT") # flash encryption

    if DEBUG_ABORT:
        print(f"{BLUE}DEBUG ABORT IS ACTIVE, THE OPERATION IS ABORTING AT THIS POINT TO PREVENT UNINTENDED FUSE BURNING{RESET}")
        return False

        # Confirm---------------------------------------------- 
        # I preferred this because espressif's burn command has you manually type BURN before actually executing the command (and its invisible so you don't know if you typo'd)
        # You can comment/remove this if you're confident in this process for faster burning
    if not confirm_burn(mac):
        print(f"{RED}User aborted{RESET}")
        sys.exit(1)

    enter_bootloader(COM_PORT) # probably not required but it helps

    #BURN BABY BURN
    burn_efuse(COM_PORT, flag_FLASH_ENCRYPTION_KEY, SPI_BOOT_CRYPT_CNT_flag)

    time.sleep(0.5)
    fuse_summary = esp_retrieve_fuse_summary(COM_PORT)
    if not fuse_summary:
        print(f"{RED}Failed to retrieve eFuse summary after burning, aborting.{RESET}")
        return False

    failFlag = False
    SPI_BOOT_CRYPT_CNT_flag = setFuseFlag(fuse_summary, "SPI_BOOT_CRYPT_CNT", True)

    if SPI_BOOT_CRYPT_CNT_flag:
        print(f"{GREEN}eFuse flags successfully programmed!{RESET}")
    else: 
        print(f"{RED}eFuse PROGRAMMING DID NOT WORK!{RESET}")
        print(f"SPI_BOOT_CRYPT_CNT_flag: {SPI_BOOT_CRYPT_CNT_flag}")
        failFlag = True

    flash_encryption_slot_burned = is_efuse_already_burned(fuse_summary, FLASH_ENCRYPTION_KEY_SLOT)
    if flash_encryption_slot_burned: failFlag = True

    if not verify_key_purpose(fuse_summary, FLASH_ENCRYPTION_KEY_SLOT, "XTS_AES_128_KEY"):
        print(f"{RED}eFuse purpose incorrect- slot {FLASH_ENCRYPTION_KEY_SLOT}{RESET}") 
        failFlag = True 

    if failFlag:
        print(f"ERROR WITH {mac}")
        mac = mac + "_ERROR"
        if LOG_MAC_ID_TO_CSV:log_to_csv(mac)
        return False
    
    # Log
    if LOG_MAC_ID_TO_CSV:log_to_csv(mac)

    print(f"{GREEN}Provisioning complete{RESET}")
    return True


if __name__ == "__main__":
    try:
        if main():
            print_rainbow("ALL DONE")
        else:
            print(f"{RED}ERROR ERROR ERROR{RESET}")
    except Exception as error:
        print(f"{RED}ERROR: {error}{RESET}", file=sys.stderr)
        sys.exit(1)