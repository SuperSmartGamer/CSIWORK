idf_component_register(SRCS "blaster.c"
                   INCLUDE_DIRS ".")

set(PRIV_REQUIRES
    "freertos"
    "nvs_flash"
    "log"
    "esp_timer"
    "esp_event"
    "esp_wifi"
    "esp_netif"
    "driver"
    "tinyusb"
)

target_link_libraries(${COMPONENT_TARGET} PRIVATE ${REQUIRES})
