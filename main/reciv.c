/*
 * PROJECT: ESP-NOW Receiver (CSI Capture)
 * TARGET: ESP32-C6
 * LOGIC: Promiscuous Mode + CSI Callback.
 */

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "driver/usb_serial_jtag.h"
#include "esp_timer.h"

#define RX_CHANNEL 6
#define BUFFER_SIZE (1024 * 120)
#define MAGIC_BYTE  0xFAFA

static const char *TAG = "ESPNOW_RX";

// Data Structures
typedef struct {
    uint16_t magic;
    uint16_t len;
    int8_t rssi;
    uint8_t channel;
    uint32_t timestamp;
} __attribute__((packed)) packet_header_t;

static uint8_t g_buffer[BUFFER_SIZE] __attribute__((aligned(4)));
static volatile uint32_t g_write_head = 0;
static volatile uint32_t g_read_head = 0;
static volatile uint32_t g_packet_count = 0;

// ASM Copy
static inline void IRAM_ATTR fast_copy_asm(void *dst, const void *src, size_t n) {
    uint32_t *d = (uint32_t *)dst;
    const uint32_t *s = (const uint32_t *)src;
    size_t count = n >> 2; 
    while (count--) *d++ = *s++;
    size_t remaining = n & 3;
    uint8_t *d8 = (uint8_t *)d;
    const uint8_t *s8 = (const uint8_t *)s;
    while (remaining--) *d8++ = *s8++;
}

// CSI Callback
void IRAM_ATTR wifi_csi_cb(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf || info->len == 0) return;

    // OPTIONAL: Filter by RSSI to remove noise if needed
    // if (info->rx_ctrl.rssi < -60) return;

    g_packet_count++;

    uint16_t data_len = info->len;
    uint16_t total_len = sizeof(packet_header_t) + data_len;
    uint32_t next_head = (g_write_head + total_len) % BUFFER_SIZE;
    
    if (next_head == g_read_head) return; 

    packet_header_t header;
    header.magic = MAGIC_BYTE;
    header.len = data_len;
    header.rssi = info->rx_ctrl.rssi;
    header.channel = info->rx_ctrl.channel;
    header.timestamp = esp_timer_get_time();

    if (g_write_head + total_len <= BUFFER_SIZE) {
        fast_copy_asm(&g_buffer[g_write_head], &header, sizeof(header));
        fast_copy_asm(&g_buffer[g_write_head + sizeof(header)], info->buf, data_len);
    } else {
        memcpy(&g_buffer[g_write_head], &header, sizeof(header)); 
    }
    g_write_head = next_head;
}

void wifi_init() {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    
    // FORCE PROMISCUOUS (To capture ESP-NOW without pairing)
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_channel(RX_CHANNEL, WIFI_SECOND_CHAN_NONE));
    
    // ENABLE CSI
    wifi_csi_config_t csi_config = { 0 }; 
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    
    ESP_LOGI(TAG, "Receiver Active on Channel %d", RX_CHANNEL);
}

void usb_flush_task(void *param) {
    static uint8_t chunk[8192]; 
    uint32_t last_log = 0;

    while (1) {
        // Heartbeat
        uint32_t now = pdTICKS_TO_MS(xTaskGetTickCount());
        if (now - last_log > 2000) {
             printf("STATUS: Captured %lu packets\n", g_packet_count);
             last_log = now;
        }

        uint32_t available = (g_write_head >= g_read_head) ? 
                             (g_write_head - g_read_head) : 
                             (BUFFER_SIZE - g_read_head + g_write_head);

        if (available > 1024) {
            uint32_t send_len = (available > sizeof(chunk)) ? sizeof(chunk) : available;
            if (g_read_head + send_len > BUFFER_SIZE) send_len = BUFFER_SIZE - g_read_head;
            memcpy(chunk, &g_buffer[g_read_head], send_len);
            
            int written = usb_serial_jtag_write_bytes(chunk, send_len, pdMS_TO_TICKS(5));
            if (written > 0) g_read_head = (g_read_head + written) % BUFFER_SIZE;
        } else {
            vTaskDelay(1); 
        }
    }
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    usb_serial_jtag_driver_config_t usb_config = {
        .tx_buffer_size = 16384, .rx_buffer_size = 16384,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
    
    wifi_init();
    
    xTaskCreatePinnedToCore(usb_flush_task, "usb_flush", 8192, NULL, 20, NULL, 0);
}