#pragma once
#include <stdint.h>

// Tiempo continuo: una muestra invalida/ocupada rompe la ventana.
class VentanaEstable {
  bool activa_ = false;
  uint32_t desde_ = 0;
 public:
  constexpr void actualizar(uint32_t now, bool condicion) {
    if (!condicion) activa_ = false;
    else if (!activa_) { activa_ = true; desde_ = now; }
  }
  constexpr bool cumplida(uint32_t now, uint32_t duracion) const {
    return activa_ && uint32_t(now - desde_) >= duracion;
  }
};

// Confirmacion de llegada y consumo de un ciclo, independiente del tag continuo.
class CicloEntrada {
  bool activo_ = false, llegada_ = false, rearmado_ = true;
 public:
  constexpr void bloquearRearme() { rearmado_ = false; }
  constexpr void iniciar() { activo_ = true; llegada_ = false; rearmado_ = false; }
  constexpr void cancelar() { activo_ = false; llegada_ = false; }
  constexpr void finalizar() { cancelar(); rearmado_ = false; }
  constexpr void actualizar(bool interiorOcupado, bool ambosLibres, bool puertaCerrada) {
    if (activo_ && interiorOcupado) llegada_ = true;
    if (!activo_ && ambosLibres && puertaCerrada) rearmado_ = true;
  }
  constexpr bool puedeCerrar(bool exteriorLibre, bool interiorOcupado, bool aperturaMinima) const {
    return activo_ && llegada_ && exteriorLibre && interiorOcupado && aperturaMinima;
  }
  constexpr bool activo() const { return activo_; }
  constexpr bool llegadaConfirmada() const { return llegada_; }
  constexpr bool rearmado() const { return rearmado_; }
};
