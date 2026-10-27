
from pathlib import Path
import secrets
import sys

KEY_FILE = Path(__file__).resolve().parent / "flash_encryption_key.bin"
KEY_SIZE = 32  # 256-bit ESP32-C3 flash-encryption key


def main():
    if KEY_FILE.exists():
        print(f"ERROR: {KEY_FILE.name} already exists.\n"*5)
        print("Move or delete the existing key before generating a new one.")
        sys.exit(1)

    key = secrets.token_bytes(KEY_SIZE)

    # Exclusive creation prevents overwriting an existing file.
    try:
        with KEY_FILE.open("xb") as file:
            file.write(key)
    except FileExistsError:
        print(f"ERROR: {KEY_FILE.name} already exists.\n"*5)
        print("Move or delete the existing key before generating a new one.")
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: Could not save encryption key: {exc}")
        sys.exit(1)

    print(f"Flash-encryption key generated: {KEY_FILE}")
    print(f"Key size: {KEY_SIZE} bytes (256 bits)")
    print("IMPORTANT: Store a secure backup of this key.  Losing this will brick your devices\n"*5)


if __name__ == "__main__":
    main()
