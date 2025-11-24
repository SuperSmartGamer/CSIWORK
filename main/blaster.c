/*
 * PROJECT: ESP-NOW High-Speed Blaster (IDF v5.x Fixed)
 * PROTOCOL: ESP-NOW (Vendor Specific Action Frames)
 * RATE: Fixed to MCS7 (OFDM) via Peer Config
 */

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_mac.h"

#define TX_CHANNEL 6
#define MAGIC_BYTE 0xFA

static const char *TAG = "ESPNOW_BLASTER";
static uint8_t broadcast_mac[ESP_NOW_ETH_ALEN] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

typedef struct {
    uint8_t magic[2];
    uint32_t seq;
} blast_payload_t;

void wifi_init() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    
    // FORCE CHANNEL
    ESP_ERROR_CHECK(esp_wifi_set_channel(TX_CHANNEL, WIFI_SECOND_CHAN_NONE));
    
    // Disconnect from any AP to ensure we stay on Channel 6
    esp_wifi_disconnect();
    
    ESP_LOGI(TAG, "WiFi Init Complete. Channel %d", TX_CHANNEL);
}

void blaster_task(void *pvParam) {
    blast_payload_t payload;
    payload.magic[0] = MAGIC_BYTE;
    payload.magic[1] = MAGIC_BYTE;
    payload.seq = 0;

    // 1. Add Broadcast Peer
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, broadcast_mac, 6);
    peerInfo.channel = TX_CHANNEL;
    peerInfo.ifidx = WIFI_IF_STA;
    peerInfo.encrypt = false;
    
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add peer");
        vTaskDelete(NULL);
    }

    // 2. FORCE RATE TO MCS7 (The Fix for v5.x)
    // This ensures we generate OFDM frames (required for CSI)
    esp_now_rate_config_t rate_config = {
        .phymode = WIFI_PHY_MODE_HT20, 
        .rate = WIFI_PHY_RATE_MCS7_SGI, 
        .ersu = false, 
        .dcm = false
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(broadcast_mac, &rate_config));
    
    ESP_LOGI(TAG, "Starting Blaster at MCS7...");

    while (1) {
        // FIRE PACKET
        esp_err_t result = esp_now_send(broadcast_mac, (uint8_t *) &payload, sizeof(payload));
        
        if (result == ESP_OK) {
            payload.seq++;
            // Speed Limit: Uncapped. 
            // If receiver crashes, uncomment the delay below.
            // vTaskDelay(1); 
        } else {
            // Buffer full? Yield.
            taskYIELD(); 
        }
    }
}

void app_main(void) {
    // NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    wifi_init();
    
    ESP_ERROR_CHECK(esp_now_init());
    
    // Universal Task Creation (Single/Dual Core Compatible)
    xTaskCreatePinnedToCore(blaster_task, "blaster", 4096, NULL, 5, NULL, tskNO_AFFINITY);
}