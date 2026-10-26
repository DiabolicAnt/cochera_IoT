package mx.ceti.smartgate

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

object Notificaciones {
    private const val CANAL_PUERTA = "puerta"
    private const val CANAL_SERVICIO = "servicio"
    const val ID_SERVICIO = 1
    private const val ID_PUERTA = 2

    fun crearCanales(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CANAL_PUERTA, "Puerta de la cochera", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Avisa cuando la cochera se abre, queda abierta o se cierra."
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CANAL_SERVICIO, "Conexión con la cochera", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Indica que la app sigue escuchando a la cochera."
            }
        )
    }

    private fun abrirApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0,
        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun servicio(context: Context, texto: String): Notification =
        NotificationCompat.Builder(context, CANAL_SERVICIO)
            .setSmallIcon(R.drawable.ic_cochera)
            .setContentTitle("SmartGate")
            .setContentText(texto)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(abrirApp(context))
            .build()

    /** Una sola notificacion de puerta que se reemplaza: abriendo -> abierta -> cerrada. */
    fun puerta(context: Context, titulo: String, texto: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val notificacion = NotificationCompat.Builder(context, CANAL_PUERTA)
            .setSmallIcon(R.drawable.ic_cochera)
            .setContentTitle(titulo)
            .setContentText(texto)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setAutoCancel(true)
            .setContentIntent(abrirApp(context))
            .build()
        NotificationManagerCompat.from(context).notify(ID_PUERTA, notificacion)
    }
}
