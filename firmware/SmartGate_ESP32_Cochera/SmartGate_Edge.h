#pragma once
#include <stdint.h>

// Media movil de cinco lecturas VALIDAS. Una invalida rompe la ventana;
// nunca incorporar 8191, ausencia de eco o una lectura vencida como distancia.
class MediaMovil {
  uint16_t valores_[5] = {};
  uint32_t suma_ = 0;
  uint8_t indice_ = 0, cantidad_ = 0;
 public:
  constexpr void limpiar() { suma_ = 0; indice_ = cantidad_ = 0; }
  constexpr void agregar(uint16_t valor) {
    if (cantidad_ == 5) suma_ -= valores_[indice_];
    else ++cantidad_;
    valores_[indice_] = valor; suma_ += valor;
    indice_ = (indice_ + 1) % 5;
  }
  constexpr uint8_t cantidad() const { return cantidad_; }
  constexpr float promedio() const { return cantidad_ ? float(suma_) / cantidad_ : 0; }
};

constexpr bool pruebaMedia() {
  MediaMovil media;
  media.agregar(100); media.agregar(200);
  if (media.promedio() != 150 || media.cantidad() != 2) return false;
  media.agregar(300); media.agregar(400); media.agregar(500); media.agregar(600);
  if (media.promedio() != 400 || media.cantidad() != 5) return false;
  media.limpiar(); media.agregar(50);
  return media.promedio() == 50 && media.cantidad() == 1;
}
static_assert(pruebaMedia(), "La media debe expulsar la muestra antigua y reiniciar tras invalido");
