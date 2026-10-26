#pragma once
// Copia este archivo como SmartGate_Gateway_Config.local.h y pon tus datos ahi.
// El archivo .local.h tiene prioridad y no debe subirse a Git.
#if __has_include("SmartGate_Gateway_Config.local.h")
#include "SmartGate_Gateway_Config.local.h"
#else
constexpr char GW_WIFI_SSID[] = "TU_RED_WIFI";      // Red de 2.4 GHz.
constexpr char GW_WIFI_PASSWORD[] = "TU_PASSWORD";
// IP LAN de la laptop con Mosquitto (ipconfig). No usar localhost.
constexpr char GW_MQTT_URI[] = "mqtt://192.168.1.100:1883";
constexpr int GW_MQTT_QOS = 1;  // 0 o 1: experimento de calidad de servicio.
#endif
