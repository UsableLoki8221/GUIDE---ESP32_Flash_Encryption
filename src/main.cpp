#include <Adafruit_NeoPixel.h>
#include "esp_flash_encrypt.h"

#define LED_PIN 4


Adafruit_NeoPixel LED1(2, LED_PIN, NEO_GRB + NEO_KHZ800);

void showLEDms(int ms, int r, int g, int b) {
  uint32_t color = LED1.Color(r, g, b);
  LED1.setPixelColor(0, color);
  LED1.setPixelColor(1, color);
  LED1.show();
  delay(ms);
}

void testFlashEncyptionStatus(bool active) {
  if (active) {

    while(1) {
/*
      bool encryptionEnabled = esp_flash_encryption_enabled();

      if (encryptionEnabled) {
        log_i("Flash encryption enabled");
        for (int i = 0; i < 2; i++) {
          showLEDms(1000, 0, 30, 0);
        }
      } else {
        log_i("Flash encryption disabled");
        for (int i = 0; i < 2; i++) {
          showLEDms(1000, 30, 0, 0);
        }
      }
*/

      esp_flash_enc_mode_t mode = esp_get_flash_encryption_mode();

      switch(mode) {
        case ESP_FLASH_ENC_MODE_DISABLED:
          log_i("Encryption disabled");
          showLEDms(1000, 30, 0, 0); //red - no encryption
          break;

        case ESP_FLASH_ENC_MODE_DEVELOPMENT:
          log_i("Encryption development mode");
          showLEDms(1000, 0, 30, 0); //green - dev mode encryption
          break;

        case ESP_FLASH_ENC_MODE_RELEASE:
          log_i("Encryption release mode");
          showLEDms(1000, 0, 0, 30); //blue - full release encryption
          break;

        default:
          log_e("Unknown encryption state");
          showLEDms(1000, 30, 30, 0); //yellow
          break;
      }
    showLEDms(1000, 0, 0, 0);
    }
  }
}

//////////////////////////////////////////////////

void setup() {
  Serial.begin(115200);
  delay(1000);


  LED1.begin();
  LED1.show();

  showLEDms(1000, 40, 40, 40);
  showLEDms(1000, 0, 0, 0);

}


void loop() {
  testFlashEncyptionStatus(1);
}