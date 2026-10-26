package mx.ceti.smartgate

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.util.Locale

private val Verde = Color(0xFF173E32)
private val Fondo = Color(0xFFF4F3EE)
private val Gris = Color(0xFF6B7268)

/** Colores de la tarjeta de la puerta: (fondo, texto). */
private fun coloresPuerta(estado: String?) = when (estado) {
    "CERRADA" -> Color(0xFFE1EDDF) to Color(0xFF1F4D33)
    "ABRIENDO", "CERRANDO" -> Color(0xFFFBEFD5) to Color(0xFF7A5210)
    "ABIERTA" -> Color(0xFFDDEBF7) to Color(0xFF1F4F78)
    "ALERTA" -> Color(0xFFF6E5D8) to Color(0xFF8A4019)
    else -> Color(0xFFE9EAE4) to Gris
}

/** Fraccion visible de la hoja: 1 = cerrada, casi 0 = abierta. */
private fun alturaPuerta(estado: String?) = when (estado) {
    "ABIERTA" -> 0.1f
    "ABRIENDO", "CERRANDO" -> 0.5f
    "ALERTA" -> 0.85f
    else -> 1f
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Notificaciones.crearCanales(this)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Verde, background = Fondo, surface = Color.White)) {
                PantallaCochera()
            }
        }
    }
}

@Composable
fun PantallaCochera() {
    val context = LocalContext.current
    val lectura by CocheraEstado.lectura.collectAsState()
    val conexion by CocheraEstado.conexion.collectAsState()
    val ultimo by CocheraEstado.ultimoMensajeMs.collectAsState()
    val activo by CocheraEstado.servicioActivo.collectAsState()
    var servidor by remember { mutableStateOf(Preferencias.servidor(context)) }
    var ahora by remember { mutableLongStateOf(System.currentTimeMillis()) }

    val permiso = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33) permiso.launch(Manifest.permission.POST_NOTIFICATIONS)
        while (true) {
            ahora = System.currentTimeMillis(); delay(1000)
        }
    }
    val edadMs = if (ultimo == 0L) null else ahora - ultimo
    val enVivo = conexion == Conexion.CONECTADA && edadMs != null && edadMs < 5000

    Column(
        Modifier
            .fillMaxSize()
            .background(Fondo)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Encabezado(conexion, enVivo)
        if (activo && !enVivo) {
            Aviso(
                if (conexion != Conexion.CONECTADA) "Sin conexión con el servidor. Revisa la IP y que el backend esté encendido."
                else "La cochera no está enviando datos. Se muestra la última lectura recibida."
            )
        }
        TarjetaPuerta(lectura)
        TarjetaCoche(lectura)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TarjetaSensor(Modifier.weight(1f), "Ultrasónico", "Entrada", lectura?.ultrasonico, "Libre")
            TarjetaSensor(Modifier.weight(1f), "ToF", "Estacionamiento", lectura?.tof, "Vacío")
        }
        if (lectura != null) {
            val edad = edadMs?.let { "hace ${it / 1000} s" } ?: "—"
            Text("Última lectura $edad · #${lectura?.secuencia}", color = Gris, fontSize = 12.sp)
        }
        TarjetaServidor(servidor, { servidor = it }, activo,
            conectar = {
                Preferencias.guardarServidor(context, servidor)
                CocheraService.iniciar(context)
            },
            desconectar = { CocheraService.detener(context) })
    }
}

@Composable
private fun Encabezado(conexion: Conexion, enVivo: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("SmartGate", fontSize = 26.sp, fontWeight = FontWeight.SemiBold, color = Verde)
            Text("Mi cochera", color = Gris, fontSize = 14.sp)
        }
        val (texto, color) = when {
            enVivo -> "En vivo" to Color(0xFF2D6142)
            conexion == Conexion.CONECTANDO -> "Conectando" to Color(0xFF7A5210)
            conexion == Conexion.CONECTADA -> "Sin señal" to Color(0xFF8A4019)
            else -> "Desconectada" to Gris
        }
        Row(
            Modifier
                .background(color.copy(alpha = 0.12f), RoundedCornerShape(50))
                .padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(8.dp).background(color, CircleShape))
            Spacer(Modifier.width(6.dp))
            Text(texto, color = color, fontSize = 13.sp, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun Aviso(texto: String) {
    Text(
        texto,
        Modifier
            .fillMaxWidth()
            .background(Color(0xFFFFF1E2), RoundedCornerShape(12.dp))
            .padding(12.dp),
        color = Color(0xFF8A4019), fontSize = 13.sp,
    )
}

@Composable
private fun TarjetaPuerta(lectura: Lectura?) {
    val estado = lectura?.puerta
    val (fondoObjetivo, texto) = coloresPuerta(estado)
    val fondo by animateColorAsState(fondoObjetivo, tween(500), label = "fondo")
    val altura by animateFloatAsState(alturaPuerta(estado), tween(900), label = "puerta")
    Card(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = fondo),
    ) {
        Column(Modifier.padding(18.dp)) {
            Text("PUERTA", color = texto.copy(alpha = 0.7f), fontSize = 12.sp, letterSpacing = 1.5.sp)
            DibujoCochera(altura, lectura?.cocheDentro == true, texto, Modifier.fillMaxWidth().height(150.dp).padding(vertical = 10.dp))
            Text(nombrePuerta(estado), color = texto, fontSize = 30.sp, fontWeight = FontWeight.SemiBold)
            if (lectura != null) {
                Text(
                    (if (lectura.reedCerrado) "El reed confirma cerrado" else "Reed: no cerrada") +
                        " · " + (if (lectura.automatico) "Modo automático" else "Modo manual"),
                    color = texto.copy(alpha = 0.8f), fontSize = 13.sp,
                )
            }
        }
    }
}

/** Fachada de la cochera: la hoja baja segun el estado y el coche aparece si esta dentro. */
@Composable
private fun DibujoCochera(alturaHoja: Float, coche: Boolean, color: Color, modifier: Modifier) {
    Canvas(modifier) {
        val trazo = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round)
        val ancho = size.minDimension * 1.5f
        val x0 = (size.width - ancho) / 2
        val alto = size.height
        val techo = Path().apply {
            moveTo(x0, alto * 0.35f); lineTo(x0 + ancho / 2, 0f); lineTo(x0 + ancho, alto * 0.35f)
        }
        drawPath(techo, color, style = trazo)
        val izq = x0 + ancho * 0.18f
        val der = x0 + ancho * 0.82f
        val arriba = alto * 0.38f
        drawLine(color, Offset(izq, alto), Offset(izq, arriba), 3.dp.toPx())
        drawLine(color, Offset(der, alto), Offset(der, arriba), 3.dp.toPx())
        drawLine(color, Offset(izq, arriba), Offset(der, arriba), 3.dp.toPx())
        drawLine(color, Offset(x0, alto), Offset(x0 + ancho, alto), 3.dp.toPx())
        if (coche) {
            val cx = (izq + der) / 2
            val ca = (der - izq) * 0.62f
            drawRoundRect(color, Offset(cx - ca / 2, alto - 34.dp.toPx()), Size(ca, 22.dp.toPx()), CornerRadius(8.dp.toPx()))
            drawRoundRect(color, Offset(cx - ca * 0.3f, alto - 48.dp.toPx()), Size(ca * 0.6f, 18.dp.toPx()), CornerRadius(8.dp.toPx()))
            drawCircle(Color.White, 7.dp.toPx(), Offset(cx - ca * 0.3f, alto - 10.dp.toPx()))
            drawCircle(color, 7.dp.toPx(), Offset(cx - ca * 0.3f, alto - 10.dp.toPx()), style = Stroke(3.dp.toPx()))
            drawCircle(Color.White, 7.dp.toPx(), Offset(cx + ca * 0.3f, alto - 10.dp.toPx()))
            drawCircle(color, 7.dp.toPx(), Offset(cx + ca * 0.3f, alto - 10.dp.toPx()), style = Stroke(3.dp.toPx()))
        }
        // Hoja de la puerta con tablillas, desde el marco superior hacia abajo.
        val hoja = (alto - arriba) * alturaHoja
        if (hoja > 2f) {
            drawRect(color.copy(alpha = 0.18f), Offset(izq + 4f, arriba + 2f), Size(der - izq - 8f, hoja - 2f))
            var y = arriba + 14.dp.toPx()
            while (y < arriba + hoja - 4f) {
                drawLine(color.copy(alpha = 0.6f), Offset(izq + 10f, y), Offset(der - 10f, y), 2.dp.toPx())
                y += 14.dp.toPx()
            }
        }
    }
}

@Composable
private fun TarjetaCoche(lectura: Lectura?) {
    val dentro = lectura?.cocheDentro == true
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(12.dp).background(
                    if (lectura == null) Color(0xFFC9CCC4) else if (dentro) Color(0xFF2D6142) else Color(0xFFB9BFB5), CircleShape
                )
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text("ESTACIONAMIENTO", color = Gris, fontSize = 11.sp, letterSpacing = 1.2.sp)
                Text(
                    when {
                        lectura == null -> "Sin datos"
                        dentro -> "Hay coche dentro"
                        else -> "No hay coche dentro"
                    },
                    fontSize = 19.sp, fontWeight = FontWeight.Medium,
                )
                if (dentro) {
                    Text(
                        if (lectura?.tofVeCoche == true) "El ToF lo detecta ahora" else "El ToF registró su entrada en el último ciclo",
                        color = Gris, fontSize = 12.sp,
                    )
                }
            }
        }
    }
}

@Composable
private fun TarjetaSensor(modifier: Modifier, nombre: String, zona: String, sensor: Sensor?, vacio: String) {
    Card(modifier, shape = RoundedCornerShape(16.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(14.dp)) {
            Text(nombre.uppercase(Locale.ROOT), color = Gris, fontSize = 11.sp, letterSpacing = 1.2.sp)
            Text(zona, color = Gris, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            val principal = when {
                sensor == null -> "—"
                sensor.valido && sensor.filtradoMm != null -> String.format(Locale.ROOT, "%.0f", sensor.filtradoMm)
                sensor.sinObjeto -> vacio
                else -> "—"
            }
            Row(verticalAlignment = Alignment.Bottom) {
                Text(principal, fontSize = 28.sp, fontWeight = FontWeight.SemiBold, color = Verde)
                if (sensor?.valido == true) {
                    Spacer(Modifier.width(4.dp)); Text("mm", color = Gris, fontSize = 13.sp, modifier = Modifier.padding(bottom = 5.dp))
                }
            }
            Text(
                when {
                    sensor == null -> "Sin datos"
                    sensor.valido -> "Crudo: ${sensor.crudoMm} mm"
                    sensor.sinObjeto -> "Sin objeto en alcance"
                    else -> "Error: ${sensor.error}"
                },
                color = Gris, fontSize = 12.sp,
            )
        }
    }
}

@Composable
private fun TarjetaServidor(
    servidor: String, cambiar: (String) -> Unit, activo: Boolean,
    conectar: () -> Unit, desconectar: () -> Unit,
) {
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("SERVIDOR", color = Gris, fontSize = 11.sp, letterSpacing = 1.2.sp)
            OutlinedTextField(
                value = servidor, onValueChange = cambiar, singleLine = true,
                label = { Text("IP de la laptop y puerto") },
                placeholder = { Text("192.168.1.100:8000") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = conectar, colors = ButtonDefaults.buttonColors(containerColor = Verde)) {
                    Text(if (activo) "Reconectar" else "Conectar")
                }
                if (activo) OutlinedButton(onClick = desconectar) { Text("Desconectar") }
            }
            Text(
                "La app sigue avisando aunque la cierres, mientras esté conectada.",
                color = Gris, fontSize = 12.sp,
            )
        }
    }
}
