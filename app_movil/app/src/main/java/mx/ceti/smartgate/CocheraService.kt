package mx.ceti.smartgate

import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * Servicio en primer plano: mantiene el WebSocket con el backend aunque la app este en
 * segundo plano, publica cada lectura para la pantalla y lanza las notificaciones de la puerta.
 */
class CocheraService : Service() {
    private val cliente = OkHttpClient.Builder().pingInterval(15, TimeUnit.SECONDS).build()
    private val handler = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var activo = false
    private var url = ""
    private var estadoPrevio: String? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACCION_DETENER) {
            detener()
            return START_NOT_STICKY
        }
        Notificaciones.crearCanales(this)
        val aviso = Notificaciones.servicio(this, "Conectando con la cochera…")
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(Notificaciones.ID_SERVICIO, aviso, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(Notificaciones.ID_SERVICIO, aviso)
        }
        url = "ws://${Preferencias.servidor(this)}/ws/telemetria?node_id=$NODO"
        activo = true
        estadoPrevio = null
        CocheraEstado.servicioActivo.value = true
        handler.removeCallbacksAndMessages(null)
        conectar()
        return START_STICKY
    }

    private fun conectar() {
        socket?.cancel()
        CocheraEstado.conexion.value = Conexion.CONECTANDO
        val peticion = try {
            Request.Builder().url(url).build()
        } catch (e: IllegalArgumentException) {
            avisoServicio("Dirección inválida: revisa IP:puerto")
            CocheraEstado.conexion.value = Conexion.DESCONECTADA
            return
        }
        socket = cliente.newWebSocket(peticion, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (webSocket !== socket) return
                CocheraEstado.conexion.value = Conexion.CONECTADA
                avisoServicio("Escuchando la cochera")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (webSocket === socket) handler.post { procesar(text) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (webSocket === socket) reintentar()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (webSocket === socket) reintentar()
            }
        })
    }

    private fun reintentar() {
        CocheraEstado.conexion.value = Conexion.DESCONECTADA
        if (!activo) return
        avisoServicio("Sin conexión con el servidor; reintentando…")
        handler.postDelayed({ if (activo) conectar() }, 3000)
    }

    private fun procesar(texto: String) {
        val lectura = try {
            Lectura.desdeJson(texto)
        } catch (e: Exception) {
            Log.w("SmartGate", "Mensaje ignorado: ${e.message}")
            return
        }
        val anterior = CocheraEstado.lectura.value
        // Ignorar un mensaje atrasado del mismo arranque (QoS 1 puede reenviar duplicados).
        if (anterior != null && anterior.nodo == lectura.nodo && anterior.arranque == lectura.arranque &&
            lectura.secuencia <= anterior.secuencia) return
        CocheraEstado.lectura.value = lectura
        CocheraEstado.ultimoMensajeMs.value = System.currentTimeMillis()
        avisarCambio(estadoPrevio, lectura.puerta)
        estadoPrevio = lectura.puerta
    }

    /** El primer mensaje solo fija el estado; las notificaciones salen en los cambios. */
    private fun avisarCambio(previo: String?, nuevo: String) {
        if (previo == null || previo == nuevo) return
        when (nuevo) {
            "ABRIENDO" -> Notificaciones.puerta(this, "Se está abriendo la cochera", "Se detectó un carro en la entrada.")
            "ABIERTA" -> Notificaciones.puerta(this, "La cochera está abierta", "La puerta terminó de subir.")
            "CERRADA" -> Notificaciones.puerta(this, "Se cerró la cochera", "El reed confirmó que la puerta quedó cerrada.")
        }
    }

    private fun avisoServicio(texto: String) {
        getSystemService(NotificationManager::class.java)
            .notify(Notificaciones.ID_SERVICIO, Notificaciones.servicio(this, texto))
    }

    private fun detener() {
        activo = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        activo = false
        handler.removeCallbacksAndMessages(null)
        socket?.close(1000, "App detenida")
        socket = null
        CocheraEstado.conexion.value = Conexion.DESCONECTADA
        CocheraEstado.servicioActivo.value = false
        super.onDestroy()
    }

    companion object {
        private const val ACCION_DETENER = "mx.ceti.smartgate.DETENER"
        private const val NODO = "cochera-01"  // node_id que publica la maqueta

        fun iniciar(context: Context) =
            context.startForegroundService(Intent(context, CocheraService::class.java))

        fun detener(context: Context) {
            context.startService(Intent(context, CocheraService::class.java).setAction(ACCION_DETENER))
        }
    }
}
