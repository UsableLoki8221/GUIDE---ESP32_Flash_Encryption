'''
Code and guide posted at:
https://github.com/UsableLoki8221/GUIDE---ESP32_Flash_Encryption
https://youtu.be/GyAqlYea8wU
'''

from pathlib import Path
import secrets
import sys

SCRIPT_DIR = Path(__file__).resolve().parent

KEY_BASE_NAME = SCRIPT_DIR / "flash_encryption_key"

KEY_FILE = KEY_BASE_NAME.with_suffix(".bin")
HEX_FILE = KEY_BASE_NAME.with_suffix(".txt")

KEY_SIZE = 32  # 32-byte;256-bit ESP32 flash-encryption key


def main():
    # Prevent accidentally overwriting an existing key or hex file.
    if KEY_FILE.exists() or HEX_FILE.exists():
        print("ERROR: An encryption key file already exists.\n" * 5)
        print("Existing files:")
        if KEY_FILE.exists():
            print(f"  - {KEY_FILE}")
        if HEX_FILE.exists():
            print(f"  - {HEX_FILE}")
        print("\nMove or delete the existing files before generating a new key.")
        sys.exit(1)

    key = secrets.token_bytes(KEY_SIZE)
    key_hex = key.hex()

    # Save the binary key without overwriting an existing file.
    try:
        with KEY_FILE.open("xb") as file:
            file.write(key)

        # Save the hexadecimal representation without overwriting.
        with HEX_FILE.open("x", encoding="ascii") as file:
            file.write(key_hex + "\n")

    except FileExistsError:
        print("ERROR: A key file already exists. No files were intentionally overwritten.")
        sys.exit(1)

    except OSError as exc:
        print(f"ERROR: Could not save encryption key: {exc}")
        sys.exit(1)

    print(f"\nBinary key saved to: {KEY_FILE}")
    print(f"Readable hexadecimal key saved to: {HEX_FILE}")
    print(f"Key size: {KEY_SIZE} bytes ({KEY_SIZE * 8} bits)")

    print("\n" + "=" * 70)
    print("FLASH-ENCRYPTION KEY (HEX)")
    print("=" * 70)
    print(key_hex)
    print("=" * 70)


    print("IMPORTANT: Store a secure backup of this key.  Losing this will lock you out from reflashing your devices\n"*5)


if __name__ == "__main__":
    main()
