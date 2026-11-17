https://youtu.be/GyAqlYea8wU

Scripts to simplify the process of using flash encryption on your ESP32 device.

Create an encryption key using generate_encryption_key.py

Save the key securely.

Read variables at the top of the python scripts and update for your use scenario.

Use burn_efuses.py to burn your key and efuse to the desired state.

Use flash_encryption_esp32.py to flash your boards.  This script effectively will replace your upload button (the script can auto upload in encrypted/plaintext by detecting the crypt fuse on the ESP)


Note: despite having encrypted firmware, OTA updates are still normally done in plaintext.  My next guide I will show how to also secure OTA files using encryption.
