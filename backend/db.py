"""Persistencia de la practica 3 con ORM asincrono (SQLAlchemy 2 + aiosqlite)."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Evento(Base):
    """Un registro por telemetria recibida, con la clase que calculo el Random Forest."""
    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    boot_id: Mapped[str] = mapped_column(String(32))
    sequence_id: Mapped[int] = mapped_column(Integer)
    variables_json: Mapped[str] = mapped_column(Text)
    clase_predicha_rf: Mapped[str | None] = mapped_column(String(40))
    probabilidad_rf: Mapped[float | None] = mapped_column(Float)
    latencia_ms: Mapped[float | None] = mapped_column(Float)
    puerta: Mapped[str] = mapped_column(String(20))
    fuente: Mapped[str] = mapped_column(String(12))
    fecha_registro: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def como_dict(self):
        # SQLite no guarda la zona horaria: la fecha se escribio en UTC y asi se devuelve.
        fecha = self.fecha_registro if self.fecha_registro.tzinfo else self.fecha_registro.replace(tzinfo=timezone.utc)
        return {c: getattr(self, c) for c in ("id", "node_id", "sequence_id", "variables_json", "clase_predicha_rf",
                                             "probabilidad_rf", "latencia_ms", "puerta", "fuente")} | {
            "fecha_registro": fecha.isoformat()}


class BaseDatos:
    def __init__(self, ruta):
        self.motor = create_async_engine(f"sqlite+aiosqlite:///{ruta}")
        self.sesiones = async_sessionmaker(self.motor, expire_on_commit=False, class_=AsyncSession)

    async def crear(self):
        async with self.motor.begin() as conexion:
            await conexion.run_sync(Base.metadata.create_all)

    async def guardar(self, **campos):
        async with self.sesiones() as sesion:
            sesion.add(Evento(**campos))
            await sesion.commit()

    async def ultimos(self, node_id, limite):
        async with self.sesiones() as sesion:
            consulta = select(Evento).where(Evento.node_id == node_id).order_by(Evento.id.desc()).limit(limite)
            return [e.como_dict() for e in (await sesion.scalars(consulta)).all()]

    async def cerrar(self):
        await self.motor.dispose()
