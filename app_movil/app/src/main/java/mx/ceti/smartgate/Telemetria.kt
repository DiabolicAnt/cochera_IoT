package mx.ceti.smartgate

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject

/** Lectura de un sensor de distancia tal como la publica la ESP32 (ya filtrada en el borde). */
data class Sensor(val valido: Boolean, val filtradoMm: Double?, val crudoMm: Int?, val error: String?) {
    /** Sin objeto dentro del alcance: entrada libre o estacionamiento vacio, no es una falla. */
    val sinObjeto get() = !valido && error == "SIN_OBJETO"

    companion object {
        fun desdeJson(o: JSONObject) = Sensor(
            valido = o.optBoolean("valid"),
            filtradoMm = if (o.isNull("filtered_mm")) null else o.getDouble("filtered_mm"),
            crudoMm = if (o.isNull("raw_mm")) null else o.getInt("raw_mm"),
            error = if (o.isNull("error")) null else o.getString("error"),
        )
    }
}

data class Lectura(
    val nodo: String,
    val arranque: String,        // boot_id: cambia cada vez que la ESP32 se reinicia
    val secuencia: Long,
    val puerta: String,          // CERRADA, ABRIENDO, ABIERTA, CERRANDO o ALERTA
    val automatico: Boolean,
    val reedCerrado: Boolean,
    val tofVeCoche: Boolean,     // vehicle.inside: el ToF lo detecta en este momento
    val entroEnCiclo: Boolean,   // vehicle.arrival: el ToF lo vio entrar en la ultima apertura
    val ultrasonico: Sensor,     // Entrada exterior (HC-SR04)
    val tof: Sensor,             // Lugar de estacionamiento (VL53L0X)
    val latenciaMs: Long?,
) {
    /** El carro estacionado puede quedar fuera del haz del ToF; la entrada registrada lo confirma. */
    val cocheDentro get() = tofVeCoche || entroEnCiclo

    companion object {
        fun desdeJson(texto: String): Lectura {
            val o = JSONObject(texto)
            val sensores = o.getJSONObject("readings")
            val puerta = o.getJSONObject("door")
            return Lectura(
                nodo = o.getString("node_id"),
                arranque = o.getString("boot_id"),
                secuencia = o.getLong("sequence_id"),
                puerta = puerta.getString("state"),
                automatico = puerta.optString("mode") == "AUTO",
                reedCerrado = puerta.optBoolean("reed_closed"),
                tofVeCoche = o.getJSONObject("vehicle").optBoolean("inside"),
                entroEnCiclo = o.getJSONObject("vehicle").optBoolean("arrival"),
                ultrasonico = Sensor.desdeJson(sensores.getJSONObject("exterior")),
                tof = Sensor.desdeJson(sensores.getJSONObject("interior")),
                latenciaMs = if (o.isNull("latency_ms")) null else o.optLong("latency_ms"),
            )
        }
    }
}

enum class Conexion { DESCONECTADA, CONECTANDO, CONECTADA }

/** Estado compartido entre el servicio (que recibe el WebSocket) y la pantalla. */
object CocheraEstado {
    val lectura = MutableStateFlow<Lectura?>(null)
    val conexion = MutableStateFlow(Conexion.DESCONECTADA)
    val ultimoMensajeMs = MutableStateFlow(0L)
    val servicioActivo = MutableStateFlow(false)
}

object Preferencias {
    private const val ARCHIVO = "smartgate"
    private const val SERVIDOR = "servidor"

    /** IP de la laptop con el backend y su puerto, por ejemplo 192.168.1.100:8000. */
    fun servidor(context: Context): String =
        context.getSharedPreferences(ARCHIVO, Context.MODE_PRIVATE).getString(SERVIDOR, "192.168.1.100:8000")!!

    fun guardarServidor(context: Context, valor: String) {
        context.getSharedPreferences(ARCHIVO, Context.MODE_PRIVATE).edit().putString(SERVIDOR, valor.trim()).apply()
    }
}

fun nombrePuerta(estado: String?) = when (estado) {
    "CERRADA" -> "Cerrada"
    "ABRIENDO" -> "Se está abriendo"
    "ABIERTA" -> "Abierta"
    "CERRANDO" -> "Se está cerrando"
    "ALERTA" -> "Cierre sin confirmar"
    null -> "Sin datos"
    else -> estado
}
