import csv
import configparser
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

# efuse summary command
#    python -m platformio pkg exec -p tool-esptoolpy -- espefuse.py --chip esp32c3 --port COM22 summary
# json form:
#   python -m platformio pkg exec -p tool-esptoolpy -- espefuse.py --chip esp32c3 --port COM22 summary --format json

ENV_NAME = "esp32-c3"           # match this to the env name you're using in your platformio.ini

COM_PORT = ""                   # leave as "" to auto-detect esp COM port, otherwise manually enter with COMxx format

UPLOAD_FS = False               # if you use a file system then set to true
ENCRYPT_FS = False              # if you want the file system also encrypted- the CSV should have the encrypted flag placed btw 
SERIAL_MONITOR = True           # view device serial monitor message directly after flashing 

FILESYSTEM_TYPE = "littlefs"    # supported fs types: "spiffs", "littlefs", "fatfs"

ENABLE_INFO = True              # script debug message viewer

PARTITION_CSV_PATH = ""         # if using a different partition scheme then set csv file path, otherwise default values used:

#UPLOAD_STATE = "plaintext"        # force upload to be encrypted or plaintext depending if this is set to "encrypt" or "plaintext".  Comment for auto-detect
#UPLOAD_STATE = "encrypt"

# THESE WILL GET OVERWRITTEN IF YOU SPECIFY A PARTITION in PARTITION_CSV_PATH above
DEFAULT_BOOTLOADER_OFFSET = 0x0
DEFAULT_PARTITION_TABLE_OFFSET = 0x8000
DEFAULT_OTADATA_OFFSET = 0xE000
DEFAULT_APP_OFFSET = 0x10000
DEFAULT_FS_OFFSET = 0x670000

PROJECT_DIR = Path(__file__).resolve().parent

FLASH_ENCRYPTION_KEY = PROJECT_DIR / "flash_encryption_key.bin" 
PLATFORMIO_INI = PROJECT_DIR / "platformio.ini"

BOOT_APP0_PATH = (
    Path.home()
    / ".platformio"
    / "packages"
    / "framework-arduinoespressif32"
    / "tools"
    / "partitions"
    / "boot_app0.bin"
)

# ANSI text colors
RED = "\033[31m"
ORANGE = "\033[38;5;208m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BLUE = "\033[34m"
PURPLE = "\033[35m"
INDIGO = "\033[38;5;54m"
VIOLET = "\033[35m"

BOLD = "\033[1m"
RESET = "\033[0m"


def info(message):
    if ENABLE_INFO:
        print(f"[INFO] {message}")

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


config = configparser.ConfigParser(interpolation=None)
if not config.read(PLATFORMIO_INI):
    raise FileNotFoundError(f"Could not read {PLATFORMIO_INI}")


def get_platformio_setting(key, fallback=None):
    section = f"env:{ENV_NAME}"
    visited = set()

    while section and section not in visited:
        visited.add(section)

        if section in config and config.has_option(section, key):
            return config.get(section, key).strip().strip('"').strip("'")

        if section not in config:
            break

        section = config.get(section, "extends", fallback="").strip()

    if config.has_section("env") and config.has_option("env", key):
        return config.get("env", key).strip().strip('"').strip("'")

    if fallback is not None:
        return fallback

    raise ValueError(
        f"Setting '{key}' not found for environment '{ENV_NAME}'"
    )


# pull upload/build settings directly from platformio.ini.
BOARD = get_platformio_setting("board", fallback="esp32-c3-devkitm-1")
BAUD_RATE = get_platformio_setting("upload_speed", fallback="921600")
FLASH_MODE = get_platformio_setting("board_build.flash_mode", fallback="dio")
FLASH_FREQ = get_platformio_setting("board_build.f_flash", fallback="80m")
FLASH_SIZE = get_platformio_setting("board_build.flash_size", fallback="4MB")


BOARD_TO_CHIP = {
    # Original ESP32
    "esp32dev": "esp32",
    "esp-wrover-kit": "esp32",

    # ESP32-C series
    "esp32-c2-devkitm-1": "esp32c2",
    "esp32-c3-devkitm-1": "esp32c3",
    "esp32-c3-devkitc-02": "esp32c3",
    "esp32-c5-devkitc-1": "esp32c5",
    "esp32-c6-devkitc-1": "esp32c6",
    "esp32-c6-devkitm-1": "esp32c6",
    "esp32-c61-devkitc-1": "esp32c61",

    # ESP32-S series
    "esp32-s2-saola-1": "esp32s2",
    "esp32-s3-devkitc-1": "esp32s3",
    "esp32-s3-devkitm-1": "esp32s3",

    # ESP32-H series
    "esp32-h2-devkitm-1": "esp32h2",
    "esp32-h4-devkitm-1": "esp32h4",
    "esp32-h21-devkitm-1": "esp32h21",

    # ESP32-P series
    "esp32-p4-function-ev-board": "esp32p4",

    # ESP32-S31 series
    "esp32-s31-devkitc-1": "esp32s31",
}


def detect_chip(board):
    board = board.strip().lower()

    if board in BOARD_TO_CHIP:
        return BOARD_TO_CHIP[board]

    patterns = (
        (r"esp32[-_]c61(?:[-_]|$)", "esp32c61"),
        (r"esp32[-_]c6(?:[-_]|$)", "esp32c6"),
        (r"esp32[-_]c5(?:[-_]|$)", "esp32c5"),
        (r"esp32[-_]c3(?:[-_]|$)", "esp32c3"),
        (r"esp32[-_]c2(?:[-_]|$)", "esp32c2"),
        (r"esp32[-_]s31(?:[-_]|$)", "esp32s31"),
        (r"esp32[-_]s3(?:[-_]|$)", "esp32s3"),
        (r"esp32[-_]s2(?:[-_]|$)", "esp32s2"),
        (r"esp32[-_]h21(?:[-_]|$)", "esp32h21"),
        (r"esp32[-_]h4(?:[-_]|$)", "esp32h4"),
        (r"esp32[-_]h2(?:[-_]|$)", "esp32h2"),
        (r"esp32[-_]p4(?:[-_]|$)", "esp32p4"),
    )

    for pattern, chip in patterns:
        if re.search(pattern, board):
            return chip

    if re.match(r"^esp32(?:[-_]|$)", board):
        return "esp32"

    raise ValueError(
        f"Cannot determine esptool chip from PlatformIO board '{board}'. "
        "Add its board ID to BOARD_TO_CHIP or update detect_chip()."
    )


CHIP = detect_chip(BOARD)




def run_command(command, cwd=PROJECT_DIR, *, capture_output=True):
    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError(
            f"Command failed ({result.returncode}):\n"
            f"{' '.join(str(part) for part in command)}\n{details}"
        )
    return result


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


def get_esptool_command(*arguments):
    return [
        "python",
        "-m",
        "platformio",
        "pkg",
        "exec",
        "-p",
        "tool-esptoolpy",
        "--",
        *arguments,
    ]


def read_efuse_summary():
    info("Retrieving efuse summary")

    command = get_esptool_command(
        "espefuse.py",
        "--chip",
        CHIP,
        "--port",
        COM_PORT,
        "summary",
        "--format",
        "json",
    )
    result = run_command(command)



    output = result.stdout
    info(output)
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(
            "espefuse did not return a valid JSON summary. "
            "Flash encryption state cannot be safely determined.\n"
            + output
        )

    try:
        summary = json.loads(output[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse eFuse JSON: {exc}") from exc

    if not isinstance(summary, dict):
        raise RuntimeError("Unexpected eFuse JSON structure.")
    return summary



def detect_flash_encryption(summary):
    counter_names = ("SPI_BOOT_CRYPT_CNT", "FLASH_CRYPT_CNT")
    counter = next(
        (summary[name] for name in counter_names if name in summary),
        None,
    )

    if counter is None:
        raise RuntimeError(
            "The JSON eFuse summary contains neither SPI_BOOT_CRYPT_CNT "
            "nor FLASH_CRYPT_CNT. Refusing to guess the encryption state."
        )

    name = counter.get("name", "Flash encryption counter")

    if counter.get("readable") is False:
        raise RuntimeError(
            f"{name} is read-protected; its state cannot be verified."
        )

    value = counter.get("value")

    # espefuse JSON can represent an unset counter as "Disable".
    if isinstance(value, str) and value.strip().lower() == "disable":
        info(f"{name} = Disable; flash encryption is DISABLED.")
        return False

    if isinstance(value, bool) or not isinstance(value, int):
        raw_value = counter.get("raw_value")

        if isinstance(raw_value, str) and raw_value.lower().startswith("0x"):
            value = int(raw_value, 16)
        else:
            raise RuntimeError(
                f"Unexpected value for {name}: {value!r}"
            )

    enabled = value.bit_count() % 2 == 1

    info(
        f"{name} = {value} (0b{value:b}); flash encryption is "
        f"{'ENABLED' if enabled else 'DISABLED'}."
    )
    return enabled



def load_partition_offsets():
    offsets = {
        "bootloader": DEFAULT_BOOTLOADER_OFFSET,
        "partition_table": DEFAULT_PARTITION_TABLE_OFFSET,
        "otadata": DEFAULT_OTADATA_OFFSET,
        "app": DEFAULT_APP_OFFSET,
        "filesystem": DEFAULT_FS_OFFSET,
    }

    if not PARTITION_CSV_PATH:
        info("No partition CSV selected; using default flash offsets.")
        return offsets

    csv_path = Path(PARTITION_CSV_PATH)
    if not csv_path.is_absolute():
        csv_path = PROJECT_DIR / csv_path
    if not csv_path.is_file():
        raise FileNotFoundError(f"Partition CSV not found: {csv_path}")

    app_candidates = []
    fs_candidates = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.reader(file):
            if not row or row[0].strip().startswith("#"):
                continue
            if len(row) < 5:
                continue

            name, part_type, subtype, offset_text, _size = [
                item.strip() for item in row[:5]
            ]
            if not name or not offset_text:
                continue

            if offset_text.lower().startswith("0x"):
                offset = int(offset_text, 16)
            elif offset_text.isdigit():
                offset = int(offset_text, 10)
            else:
                continue

            if part_type.lower() == "app":
                app_candidates.append((subtype.lower() == "factory", offset, name))

            if (
                part_type.lower() == "data"
                and subtype.lower() in {"spiffs", "littlefs", "fat", "fatfs"}
            ) or name.lower() in {"spiffs", "littlefs", "filesystem", "storage"}:
                fs_candidates.append((offset, name))

    if app_candidates:
        factory = [item for item in app_candidates if item[0]]
        offsets["app"] = (factory[0] if factory else app_candidates[0])[1]

    if UPLOAD_FS:
        if not fs_candidates:
            raise RuntimeError(
                f"No filesystem partition was recognized in {csv_path}. "
                "Use a partition name such as spiffs/littlefs/filesystem, "
                "or leave PARTITION_CSV_PATH empty to use the default offset."
            )
        offsets["filesystem"] = fs_candidates[0][0]

    info(
        "Flash offsets: "
        + ", ".join(f"{name}=0x{value:X}" for name, value in offsets.items())
    )
    return offsets


def build_images():
    env_name = ENV_NAME
    filesystem_type = FILESYSTEM_TYPE.lower().strip()

    command = [
        "python", "-m", "platformio", "run", "-e", env_name
    ]
    info(f"Building PlatformIO environment '{env_name}'...")
    run_command(command)

    build_dir = PROJECT_DIR / ".pio" / "build" / env_name
    images = {
        "bootloader": build_dir / "bootloader.bin",
        "partition_table": build_dir / "partitions.bin",
        "app": build_dir / "firmware.bin",
        "otadata": BOOT_APP0_PATH,
    }

    for label, path in images.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} binary not found: {path}")

    if UPLOAD_FS:
        fs_command = [
            "python", "-m", "platformio", "run",
            "-e", env_name, "-t", "buildfs",
        ]
        info("Building filesystem image...")
        run_command(fs_command)

        fs_names = {
            "spiffs": "spiffs.bin",
            "littlefs": "littlefs.bin",
            "fatfs": "fatfs.bin",
        }
        fs_name = fs_names.get(filesystem_type)
        if not fs_name:
            raise RuntimeError(
                f"Unsupported board_build.filesystem value: {filesystem_type}"
            )
        fs_path = build_dir / fs_name
        if not fs_path.is_file():
            raise FileNotFoundError(f"Filesystem image not found: {fs_path}")
        images["filesystem"] = fs_path

    return images


def encrypt_binaryyyy(input_path, offset):
    input_path = Path(input_path)
    if not FLASH_ENCRYPTION_KEY.is_file():
        raise FileNotFoundError(
            f"Flash-encryption key not found: {FLASH_ENCRYPTION_KEY}"
        )

    output_path = input_path.with_name(f"{input_path.stem}_encrypted{input_path.suffix}")
    command = get_esptool_command(
        "espsecure",
        "encrypt_flash_data",
        "--aes-xts",
        "--keyfile",
        str(FLASH_ENCRYPTION_KEY),
        "--address",
        hex(offset),
        "--output",
        str(output_path),
        str(input_path),
    )
    info(f"Encrypting {input_path.name} for flash address 0x{offset:X}...")
    run_command(command)
    if not output_path.is_file():
        raise RuntimeError(f"Encryption output was not created: {output_path}")
    return output_path


def encrypt_binary(input_path, offset):
    output_path = input_path.with_name(f"{input_path.stem}_encrypted{input_path.suffix}")

    if not os.path.exists(FLASH_ENCRYPTION_KEY):
        raise FileNotFoundError("flash_encryption_key.bin not found")

    if output_path.exists():
        age = time.time() - os.path.getmtime(output_path)
        if age > 10:
            print(f"[INFO] Encrypted image too old ({age:.1f}s), deleting and rebuilding...")
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass

    cmd = [
        "python",
        "-m",
        "platformio",
        "pkg",
        "exec",
        "-p",
        "tool-esptoolpy",
        "--",
        #"espsecure.py",
        r"C:\Espressif\frameworks\esp-idf-v5.4.1\components\esptool_py\esptool\espsecure.py", # this was most important part to successfully getting file encryption to work, built in platformio tool did not work like this did
        "encrypt_flash_data",
        "--aes-xts",
        "--keyfile", str(FLASH_ENCRYPTION_KEY),
        "--address", str(offset),
        "--output", str(output_path),
        str(input_path)
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    if (False):
        for line in process.stdout:
            print(line, end='')

    process.wait()

    if process.returncode != 0:
        raise RuntimeError(f"Encryption failed with code {process.returncode}")

    print(f"{GREEN}Encryption completed successfully - {output_path}{RESET}")

    return output_path

def flash_images(images, offsets, flash_encryption):
    if flash_encryption:
        images = dict(images)
        for label in ("bootloader", "partition_table", "otadata", "app"):
            images[label] = encrypt_binary(images[label], offsets[label])
        if UPLOAD_FS and ENCRYPT_FS:
            images["filesystem"] = encrypt_binary(
                images["filesystem"], offsets["filesystem"]
            )
        elif UPLOAD_FS:
            info(
                "Filesystem will be written unencrypted because ENCRYPT_FS=False. "
                "Confirm that this matches the partition CSV scheme-flag must be set according to espressif."
            )
    print(f"FLASH_MODE: {FLASH_MODE}")
    print(f"FLASH_FREQ: {FLASH_FREQ}")

    command = get_esptool_command(
        "esptool.py",
        "--chip", CHIP,
        "--port", COM_PORT,
        "--baud", BAUD_RATE,
        "--before", "default_reset",
        "--after", "hard_reset",
        "write-flash",
        "--flash-mode", FLASH_MODE,
        "--flash-freq", FLASH_FREQ,
        "--flash-size", FLASH_SIZE,
    )

    if not flash_encryption:
        command.append("-z")

    pairs = [
        ("bootloader", offsets["bootloader"]),
        ("partition_table", offsets["partition_table"]),
        ("otadata", offsets["otadata"]),
        ("app", offsets["app"]),
    ]
    if UPLOAD_FS:
        pairs.append(("filesystem", offsets["filesystem"]))

    for label, offset in pairs:
        command.extend([hex(offset), str(images[label])])

    command.append("--force")

    info("Flashing firmware to the ESP32...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )
    output = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            output.append(line)
        return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise

    if return_code != 0:
        raise RuntimeError(
            "Flashing failed:\n" + "".join(output)
        )

    print(f"{GREEN}{BOLD}Firmware flashing completed successfully.{RESET}")


def serial_monitor():
    print("\nOpening serial monitor. Press Ctrl+C to exit.\n")
    try:
        with serial.Serial(COM_PORT, 115200, timeout=0.2) as connection:
            while True:
                data = connection.readline()
                if data:
                    print(data.decode("utf-8", errors="replace"), end="")
    except KeyboardInterrupt:
        print("\nSerial monitor closed.")


def main():
    resolve_com_port() # sets global COM port value
    offsets = load_partition_offsets()

    summary = read_efuse_summary()

    if UPLOAD_STATE == "encrypt":
        print("UPLOAD_STATE == 'encrypt'- Forcing upload to encrypt")
        flash_encryption = True
    elif UPLOAD_STATE == "plaintext":
        print("UPLOAD_STATE == 'plaintext'- Forcing upload to plaintext")
        flash_encryption = False
    else:
        flash_encryption = detect_flash_encryption(summary)

    images = build_images()
    flash_images(images, offsets, flash_encryption)
    
    print_rainbow("SUCCESS")
    print_rainbow("ALL DONE")

    if SERIAL_MONITOR:
        serial_monitor()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{RED}ERROR: {error}{RESET}", file=sys.stderr)
        sys.exit(1)







FLASH_ENCRYPTION = True

def note():
    if FLASH_ENCRYPTION:
        
        #bootloader_path = r"C:\Espressif\frameworks\esp-idf-v5.4.1\flash_encryption_dev\build\bootloader\bootloader.bin" # NOT NECESSARY # FOR TESTING
        bootloader_path = encrypt_binary(
            bootloader_path,
            "bootloader_encrypted.bin",
            "0x0"
        )
        
        #partitions_path = r"C:\Espressif\frameworks\esp-idf-v5.4.1\flash_encryption_dev\build\partition_table\partition-table.bin" # NOT NECESSARY # FOR TESTING
        partitions_path = encrypt_binary(
            partitions_path,
            "partitions_encrypted.bin",
            "0x8000"
        )
        #boot_app0_path = r"C:\Espressif\frameworks\esp-idf-v5.4.1\flash_encryption_dev\build\ota_data_initial.bin" # NOT NECESSARY # FOR TESTING
        boot_app0_path = encrypt_binary(
            boot_app0_path,
            "otadata_encrypted.bin",
            "0xe000"
        )
        #firmware_path = r"C:/Espressif/frameworks/esp-idf-v5.4.1/flash_encryption_dev/build/flash_encryption_dev.bin" # FOR TESTING
        print("1")
        print(firmware_path)
        firmware_path = encrypt_binary(
            firmware_path,
            "firmware_encrypted.bin",
            "0x10000"
        )
        print("2")
        print(firmware_path)

    print(f"{BLUE}ABOUT TO FLASH ZADDY{RESET}")
    flash_cmd = [
        "python", "-m", "platformio", "pkg", "exec",
        "-p", "tool-esptoolpy", "--",
        "esptool",
        "--chip", "esp32s3",
        "--port", COM_PORT,
        "--baud", "921600",
        "--before", "default_reset",
        "--after", "hard_reset",
        "write_flash",
    ]
    if not FLASH_ENCRYPTION:
        flash_cmd.append("-z")
    flash_cmd += [
        "--flash_mode", "dio",
        "--flash_freq", "80m",
        "--flash_size", "8MB",

        "0x0", bootloader_path,
        "0x8000", partitions_path,
        "0xe000", boot_app0_path,
        "0x10000", firmware_path
    ]

