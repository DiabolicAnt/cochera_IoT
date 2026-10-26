from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Reading(Contract):
    raw_mm: int | None = Field(ge=0, le=65535)
    filtered_mm: float | None = Field(ge=0, le=4000)
    valid: bool
    sample_count: int = Field(ge=0, le=5)
    error: str | None = Field(max_length=80)

    @model_validator(mode="after")
    def consistency(self):
        if self.valid and (self.raw_mm is None or self.filtered_mm is None or self.sample_count == 0 or self.error is not None):
            raise ValueError("Lectura valida requiere raw, promedio, muestras y error null")
        if not self.valid and (self.filtered_mm is not None or self.sample_count != 0 or self.error is None):
            raise ValueError("Lectura invalida requiere promedio null, cero muestras y motivo")
        return self


class Readings(Contract):
    exterior: Reading
    interior: Reading


class Door(Contract):
    state: Literal["DESCONOCIDA", "CERRADA", "ABRIENDO", "APERTURA_ORDENADA", "ABIERTA", "CERRANDO",
                   "CALIBRACION", "ALERTA", "FALLO"]
    mode: Literal["AUTO", "MANUAL"]
    reed_closed: bool
    servo_command_deg: int = Field(ge=0, le=180)


class Vehicle(Contract):
    inside: bool
    arrival: bool
    rearmed: bool
    exterior_clear: bool


class Tag(Contract):
    present: bool
    rssi_dbm: int = Field(ge=-127, le=0)


class Network(Contract):
    wifi_connected: bool
    mqtt_connected: bool
    heap_free: int = Field(ge=0)
    # Agregados por el gateway C3; opcionales para el simulador y el firmware anterior.
    wifi_rssi_dbm: int | None = Field(default=None, ge=-127, le=0)
    qos: Literal[0, 1] | None = None
    publish_us: int | None = Field(default=None, ge=0, le=60_000_000)


class Data(Contract):
    """Vector multivariable de la practica 3 (entrada del Random Forest)."""
    ultrasonico_mm: float | None = Field(ge=0, le=4000)   # 4000 = sin objeto; null = invalida
    tof_mm: float | None = Field(ge=0, le=2000)           # 2000 = sin objeto; null = invalida
    reed_cerrado: Literal[0, 1]
    apertura_pct: float = Field(ge=0, le=100)


class Control(Contract):
    periodo_ms: int = Field(ge=50, le=10000)
    ultimo_cmd: int = Field(ge=0, le=4294967295)  # id del ultimo comando ejecutado por la ESP32


class Telemetry(Contract):
    schema_version: Literal[1]
    node_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    boot_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    sequence_id: int = Field(ge=0, le=4294967295)
    timestamp: int | None = Field(ge=1700000000000)
    time_synced: bool
    uptime_ms: int = Field(ge=0, le=4294967295)
    source: Literal["hardware", "simulation"]
    filter_window: Literal[5]
    readings: Readings
    door: Door
    vehicle: Vehicle
    tag: Tag | None = None  # Sin BLE en la version con gateway C3.
    network: Network
    # Practica 3: hora de envio (NTP del gateway), variables para el modelo y estado del canal de control.
    timestamp_ms: int | None = Field(default=None, ge=1700000000000)
    data: Data | None = None
    control: Control | None = None

    @model_validator(mode="after")
    def clock_and_ranges(self):
        if self.time_synced != (self.timestamp is not None):
            raise ValueError("timestamp null si el reloj no esta sincronizado")
        for reading, minimum, maximum in [(self.readings.exterior, 20, 4000), (self.readings.interior, 0, 2000)]:
            if reading.valid and not (minimum <= reading.raw_mm <= maximum and minimum <= reading.filtered_mm <= maximum):
                raise ValueError("Distancia valida fuera del rango del sensor")
        return self


ACCIONES = ("abrir", "cerrar", "auto", "manual", "periodo")
PERIODOS = (100, 500, 1000)


class Comando(Contract):
    """Orden del dashboard hacia la ESP32 (Web -> FastAPI -> MQTT -> gateway -> UART)."""
    accion: Literal["abrir", "cerrar", "auto", "manual", "periodo"]
    valor: int | None = None

    @model_validator(mode="after")
    def valor_valido(self):
        if self.accion == "periodo" and self.valor not in PERIODOS:
            raise ValueError("periodo admite 100, 500 o 1000 ms")
        if self.accion != "periodo" and self.valor is not None:
            raise ValueError("solo periodo lleva valor")
        return self


class Availability(Contract):
    node_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    boot_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    online: bool
