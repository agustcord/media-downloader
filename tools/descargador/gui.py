"""Interfaz gráfica (tkinter) de Media Downloader.

Estética derivada, no impuesta
------------------------------
El proyecto hermano ``visual-audio`` ya tiene un sistema de diseño propio
(Dark Zinc + Cian/Violeta, Segoe UI, contrastes medidos). **Este módulo lo
extiende en vez de reemplazarlo**: mismas superficies y misma escala
tipográfica, pero con el registro de una **herramienta de uso personal**, no el
de un lienzo creativo: una sola columna, pasos numerados, una única acción
primaria y el resultado al final. Sin gradientes, sin animaciones decorativas.

Las cuatro correcciones de UI
-------------------------------------------------------------
1. **Selector de calidad: el estado elegido se ve de un vistazo.** El radio
   redondo con "fondo azul y un punto blanco" se reemplaza por un bloque:
   seleccionado → fondo cian **lleno**, texto casi negro en negrita y un
   glifo ``●``; sin seleccionar → bloque oscuro con ``○``. Tres señales
   redundantes (relleno, peso, glifo), ninguna depende sólo del color.
2. **Sin caja de registro.** La caja de texto permanente desaparece; queda un
   **espacio de estado**: una línea con el resultado ("Descarga finalizada:
   x.mp4" / "Error: <causa>") y una línea de detalle. El ``stderr`` del motor
   sigue alcanzable: por *tooltip* sobre el estado y por archivo
   (``descargador.log``), nunca ocupando la ventana.
3. **El gris nunca más sobre el cian.** Medido: el texto gris de la corrida en
   curso quedaba a **1.06:1** contra el cian de la barra, y la etiqueta del
   botón Descargar deshabilitado a **1.05:1** (gris ``#9ca3af`` sobre cian
   ``#06b6d4``): ilegibles los dos. Regla del sistema a partir de ahora:
   **el gris vive sólo sobre superficie oscura; el texto en curso va en
   blanco.** Descargar deshabilitado pasa a fondo oscuro, no cian.
4. **Cancelar** (D12): botón propio, deshabilitado en ``idle`` y normal sólo
   mientras corre una descarga. Consume la API del núcleo sin cambiarla.

Contrastes calculados (no estimados a ojo), función WCAG 2.1 sobre ``#09090b``
salvo que se indique: ``#fafafa`` 19.06:1 · ``#f4f4f5`` 18.13:1 ·
``#a1a1aa`` 7.76:1 · ``#f87171`` 7.19:1 · ``#f59e0b`` 9.26:1 · ``#10b981``
7.84:1 · ``#22d3ee`` 11.01:1. Sobre ``#27272a``: ``#f4f4f5`` 13.55:1 ·
``#9ca3af`` 5.87:1. Sobre ``#06b6d4``: ``#09090b`` 8.19:1. Todos superan AA
(4.5:1 texto normal). Estos números los re-verifica ``tests\\test_gui.py``
(``test_contraste_*``): un contraste que no cumple es un defecto medible.

Regla de concurrencia (invariantes I1–I3)
-----------------------------------------
- El estado ``self.estado`` vive aquí, en la capa que tiene el estado: el núcleo
  es sin estado a propósito, así que "no hay dos descargas simultáneas" **no es
  medible sobre ``core``**. La App es la que rechaza la segunda (I1).
- El hilo de trabajo **nunca toca un widget**: sólo encola eventos. El único
  lugar que muta la interfaz es ``_aplicar``, invocado desde ``poll`` en el hilo
  principal (I2). Los manejadores de botones leen y encolan, nunca escriben.
- Tras un resultado —éxito, fallo, cancelación o *timeout*— el estado vuelve a
  ``idle`` (I3, I9).

Lo que esta interfaz **no** promete
-----------------------------------
- **Historias de Instagram: no soportadas** (D8).
- **Carruseles de Instagram: no verificado** (D7′); se entrega lo que el motor dé.
- **Cookies de navegador: no se ofrecen.** Los navegadores actuales cifran las
  cookies con App-Bound Encryption (R8), así que la única vía activable es un
  archivo Netscape exportado a mano, y arranca desactivada.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from . import core
from .config import RUTA_PROYECTO, Config, Opciones, resolver_salida
from .core import MENSAJE_CANCELADO, Resultado
from .engine import YtdlpEngine
from .platforms import detectar, normalizar
from .verify import streams_de

# ---------------------------------------------------------------------------
# Sistema de diseño (tokens). Extiende el de visual-audio; no lo reemplaza.
# ---------------------------------------------------------------------------

T: dict[str, str] = {
    # Superficies (Dark Zinc)
    "app": "#09090b",
    "panel": "#18181b",
    "elevado": "#27272a",
    "hover": "#3f3f46",
    "borde": "#3f3f46",
    "borde_sutil": "#27272a",
    # Acentos
    "cian": "#06b6d4",
    "cian_hover": "#22d3ee",
    "violeta": "#8b5cf6",
    # Estados (semántica de color estable en toda la app)
    "ok": "#10b981",
    "aviso": "#f59e0b",
    "error": "#f87171",
    # Texto
    "titular": "#fafafa",
    "texto": "#f4f4f5",
    "muted": "#a1a1aa",
    "sobre_cian": "#09090b",
    "inactivo": "#9ca3af",
}

FUENTE_UI: tuple[str, int] = ("Segoe UI", 9)
FUENTE_UI_BOLD: tuple[str, int, str] = ("Segoe UI", 9, "bold")
FUENTE_TITULAR: tuple[str, int, str] = ("Segoe UI", 11, "bold")
FUENTE_SECCION: tuple[str, int, str] = ("Segoe UI", 9, "bold")
FUENTE_BOTON: tuple[str, int, str] = ("Segoe UI", 10, "bold")
FUENTE_ESTADO: tuple[str, int, str] = ("Segoe UI", 10, "bold")
FUENTE_DETALLE: tuple[str, int] = ("Segoe UI", 9)
FUENTE_LOG: tuple[str, int] = ("Consolas", 9)

ETIQUETAS_PLATAFORMA: dict[str, str] = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

#: D8 y D7′, en texto visible. Son los dos `grep` que audita el criterio 6, así
#: que las dos frases literales ("no soportadas", "no verificado") van acá tal
#: cual: nadie las puede "mejorar" sin romper el criterio.
AVISO_LIMITACIONES = (
    "Historias de Instagram: no soportadas. "
    "Carruseles: no verificado — se descarga lo que entregue el motor."
)

#: R8, en texto visible y sin nombrar la flag que no ofrecemos.
NOTA_COOKIES = (
    "Sin cookies la descarga es anónima. La vía de navegador no se ofrece: esta "
    "máquina cifra las cookies con App-Bound Encryption y el motor no puede leerlas."
)

#: Corrección 1: el glifo es la señal que **no** depende del color. Con el
#: relleno y la negrita son tres, y el estado se lee aunque el monitor mienta.
GLIFO_MARCADO = "\u25cf"  # ●
GLIFO_VACIO = "\u25cb"  # ○

#: Corrección 2/D12: el *timeout* ya llega con los minutos que calcula el
#: núcleo; la GUI agrega **dónde** se cambia ese número.
HINT_TIMEOUT_UI = (
    "El límite se cambia en config.json → timeout_segundos (minutos)."
)

#: El detalle del motor ya no vive en una caja: va a este archivo y al tooltip.
RUTA_LOG_POR_DEFECTO: Path = RUTA_PROYECTO / "descargador.log"


@dataclass(frozen=True)
class Calidad:
    """Una opción del selector de calidad."""

    etiqueta: str
    detalle: str
    opciones: Opciones


CALIDADES: tuple[Calidad, ...] = (
    Calidad("Mejor hasta 1080p", "video + audio, MP4", Opciones(modo="video", max_height=1080)),
    Calidad("Hasta 720p", "video + audio, MP4", Opciones(modo="video", max_height=720)),
    Calidad("Solo audio MP3", "sin video", Opciones(modo="audio", audio_format="mp3")),
    Calidad("Solo audio M4A", "sin video", Opciones(modo="audio", audio_format="m4a")),
)

CALIDAD_POR_DEFECTO = CALIDADES[0].etiqueta

SIN_COOKIES = "sin"
CON_ARCHIVO = "archivo"


def _mmss(segundos: float) -> str:
    """Segundos a ``m:ss``."""
    total = int(segundos)
    return f"{total // 60}:{total % 60:02d}"


class TooltipDetalle:
    """Detalle del motor **bajo demanda**: no es una caja permanente en la UI.

    Reemplaza a la caja de registro. Aparece tras ``ESPERA_MS`` sobre el
    espacio de estado y muestra las últimas líneas de yt-dlp. El contenido
    completo queda en el archivo de registro, cuya ruta también se muestra.
    """

    ESPERA_MS = 400
    LINEAS = 12
    ANCHO = 760

    def __init__(self, widget: tk.Misc, proveedor: Callable[[], str]) -> None:
        self.widget = widget
        self.proveedor = proveedor
        self._ventana: tk.Toplevel | None = None
        self._id: str | None = None
        widget.bind("<Enter>", self._al_entrar, add="+")
        widget.bind("<Leave>", self._al_salir, add="+")
        widget.bind("<ButtonPress>", self._al_salir, add="+")

    def _al_entrar(self, _event: object = None) -> None:
        self._cancelar()
        self._id = self.widget.after(self.ESPERA_MS, self._mostrar)

    def _al_salir(self, _event: object = None) -> None:
        self._cancelar()
        self._ocultar()

    def _cancelar(self) -> None:
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except tk.TclError:
                pass
            self._id = None

    def _mostrar(self) -> None:
        self._id = None
        texto = self.proveedor().strip()
        if not texto:
            return
        self._ocultar()
        ventana = tk.Toplevel(self.widget)
        ventana.wm_overrideredirect(True)
        ventana.configure(bg=T["borde"])
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        except tk.TclError:
            x = y = 0
        ventana.wm_geometry(f"+{x}+{y}")
        tk.Label(
            ventana,
            text=texto,
            bg=T["panel"],
            fg=T["texto"],
            font=FUENTE_LOG,
            justify="left",
            anchor="w",
            wraplength=self.ANCHO,
            padx=8,
            pady=6,
        ).pack()
        self._ventana = ventana

    def _ocultar(self) -> None:
        if self._ventana is not None:
            try:
                self._ventana.destroy()
            except tk.TclError:
                pass
            self._ventana = None


class App:
    """Ventana principal del descargador.

    ``App(root, cfg, engine)`` es la firma que consume ``__main__.py``. No lee
    la red al construirse: lo único que consulta es la versión del binario, y si
    el binario no responde muestra "no disponible" en vez de caerse.

    ``ruta_log`` es opcional y existe para que los tests no escriban en el
    proyecto: por defecto el registro queda junto a ``config.json``.
    """

    INTERVALO_POLL_MS = 60
    INTERVALO_BARRA_MS = 12
    DETALLE_MAX = 400

    def __init__(
        self,
        root: tk.Tk,
        cfg: Config,
        engine: YtdlpEngine,
        ruta_log: Path | str | None = None,
    ) -> None:
        self.root = root
        self.cfg = cfg
        self.engine = engine
        self.ruta_log = Path(ruta_log) if ruta_log is not None else RUTA_LOG_POR_DEFECTO

        self.cola: queue.Queue = queue.Queue()
        self.estado: str = "idle"  # "idle" | "ocupado" → I1 e I3 se miden aquí
        self.ultimo: Resultado | None = None  # último Resultado, auditable
        self._hilo: threading.Thread | None = None
        self._determinada = False
        self._id_after: str | None = None
        self._version: str = self._version_del_motor()

        #: D12: se crea **antes** de lanzar el hilo; el botón hace ``.set()``.
        self._cancel: threading.Event | None = None
        #: Etiqueta de la corrida en curso ("Descargando de YouTube").
        self._etiqueta_corrida: str = ""
        #: Detalle de la corrida (sin caja en pantalla): tooltip + archivo.
        self.detalle: deque[str] = deque(maxlen=self.DETALLE_MAX)
        self._radios_calidad: list[tuple[tk.Radiobutton, Calidad]] = []
        self._radios_cookies: list[tuple[tk.Radiobutton, str]] = []

        self._construir()

        self.cola.put(("log", (f"yt-dlp {self._version} · salida: {resolver_salida(cfg)}", "muted")))
        self.cola.put(("log", ("Pegá un link y pulsá Inspeccionar o Descargar.", "muted")))
        self.poll()

    # ------------------------------------------------------------------ estado

    def _version_del_motor(self) -> str:
        """Versión de yt-dlp. Nunca rompe la ventana: si falla, lo declara."""
        try:
            return self.engine.version().strip() or "desconocida"
        except Exception:  #MotorError si el binario falta; FileNotFoundError; etc.
            return "no disponible"

    def poll(self) -> None:
        """Drena la cola y reprograma. Es el **único** camino hacia los widgets (I2)."""
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                self._aplicar(tipo, dato)
        except queue.Empty:
            pass
        self._id_after = self.root.after(self.INTERVALO_POLL_MS, self.poll)

    # ------------------------------------------------------------- operaciones

    def iniciar(self, url: str, op: Opciones, cfg: Config | None = None) -> bool:
        """Arranca una descarga si no hay otra en curso (I1).

        Devuelve ``False`` sin encolar trabajo cuando ya está ocupada o cuando el
        link no corresponde a una plataforma soportada: el rechazo se ve en el
        estado, no se disfraza de éxito.
        """
        if self.estado != "idle":
            self.cola.put(("log", ("Ya hay una operación en curso: esperá a que termine.", "aviso")))
            return False
        try:
            limpia = normalizar(url)
            plataforma = detectar(limpia)
        except ValueError as exc:
            self.cola.put(("log", (f"Link no reconocido: {exc}", "error")))
            return False

        cfg = cfg if cfg is not None else self._cfg_efectivo()
        # D12: el evento nace antes que el hilo y se le entrega por parámetro:
        # el hilo no lo puede perder por una reasignación posterior.
        cancel = threading.Event()
        self._cancel = cancel
        self._etiqueta_corrida = (
            f"Descargando de {ETIQUETAS_PLATAFORMA.get(plataforma, plataforma)}"
        )
        self.estado = "ocupado"
        self.cola.put(("ocupado", (self._etiqueta_corrida, True)))
        self._lanzar_hilo(lambda: self._trabajar_descarga(limpia, op, cfg, cancel))
        return True

    def inspeccionar(self, url: str, cfg: Config | None = None) -> bool:
        """Identifica el material sin descargarlo. Mismas guardas que ``iniciar``.

        La inspección **no** es cancelable: ``core.inspect_url`` no expone
        ``cancel`` y el núcleo no se toca en este paso. El botón Cancelar queda
        deshabilitado mientras inspecciona en vez de mentir.
        """
        if self.estado != "idle":
            self.cola.put(("log", ("Ya hay una operación en curso: esperá a que termine.", "aviso")))
            return False
        try:
            limpia = normalizar(url)
            detectar(limpia)
        except ValueError as exc:
            self.cola.put(("log", (f"Link no reconocido: {exc}", "error")))
            return False

        cfg = cfg if cfg is not None else self._cfg_efectivo()
        self._cancel = None
        self._etiqueta_corrida = "Inspeccionando el link"
        self.estado = "ocupado"
        self.cola.put(("ocupado", ("Inspeccionando el link", False)))
        self._lanzar_hilo(lambda: self._trabajar_inspeccion(limpia, cfg))
        return True

    def _lanzar_hilo(self, objetivo: Callable[[], None]) -> None:
        """Único punto de la App donde se crea un hilo (seam que usa la sonda)."""
        self._hilo = threading.Thread(target=objetivo, name="descargador-trabajo", daemon=True)
        self._hilo.start()

    # ------------------------------------------------------- cuerpo del trabajo

    def _trabajar_descarga(
        self, url: str, op: Opciones, cfg: Config, cancel: threading.Event
    ) -> None:
        """Cuerpo del hilo de descarga. **No toca widgets:** sólo encola."""

        def avance(pct: float | None, linea: str) -> None:
            if linea.strip():
                self.cola.put(("log", (linea.rstrip(), "consola")))
            if pct is not None:
                self.cola.put(("progreso", pct))

        try:
            res = core.download(
                url,
                op,
                cfg,
                self.engine,
                on_progress=avance,
                timeout_seg=cfg.timeout_segundos,
                cancel=cancel,
            )
        except Exception as exc:  # red de seguridad: el hilo nunca muere en silencio
            res = Resultado(ok=False, error=f"Fallo inesperado durante la descarga: {exc!r}")
        self.cola.put(("fin", res))

    def _trabajar_inspeccion(self, url: str, cfg: Config) -> None:
        """Cuerpo del hilo de inspección. **No toca widgets:** sólo encola."""
        try:
            datos = core.inspect_url(url, cfg, self.engine)
        except Exception as exc:
            self.cola.put(("log", (f"No se pudo inspeccionar el link: {exc}", "error")))
        else:
            self.cola.put(("inspeccion", datos))
        self.cola.put(("fin", None))

    # ------------------------------------------------------------ configuración

    def _cfg_efectivo(self) -> Config:
        """Config con la elección de cookies del usuario.

        ``core.download`` construye su ``CookieSpec`` desde la ``Config`` (no
        recibe cookies por parámetro), así que la forma de pasarle la elección
        sin tocar el núcleo es derivar una Config nueva. Con la opción por
        defecto no cambia nada y por tanto **no se inyecta ninguna flag** (I8).
        """
        if self.cookies_var.get() != CON_ARCHIVO:
            return self.cfg
        ruta = self.ruta_cookies_var.get().strip()
        if not ruta:
            self.cola.put(("log", ("Elegiste archivo de cookies pero no pusiste la ruta: se descarga sin cookies.", "aviso")))
            return self.cfg
        if not os.path.isfile(ruta):
            self.cola.put(("log", (f"El archivo de cookies no existe ({ruta}): se descarga sin cookies.", "aviso")))
            return self.cfg
        self.cola.put(("log", (f"Usando cookies de {ruta}", "muted")))
        return replace(self.cfg, cookies_file=ruta)

    # -------------------------------------------------------------- aplicación

    def _aplicar(self, tipo: str, dato: object) -> None:
        """Traduce un evento de la cola a widgets. Sólo corre en el hilo principal."""
        if tipo == "log":
            texto, tag = dato  # type: ignore[misc]
            self._escribir_log(texto, tag)
        elif tipo == "ocupado":
            texto, cancelable = dato  # type: ignore[misc]
            self._al_ocuparse(str(texto), bool(cancelable))
        elif tipo == "cancelando":
            self._al_mostrar_cancelando()
        elif tipo == "progreso":
            self._al_progresar(float(dato))  # type: ignore[arg-type]
        elif tipo == "inspeccion":
            self._mostrar_inspeccion(dict(dato))  # type: ignore[arg-type]
        elif tipo == "fin":
            self._al_terminar(dato if isinstance(dato, Resultado) else None)  # type: ignore[arg-type]

    def _al_ocuparse(self, texto: str, cancelable: bool = False) -> None:
        # Corrección 3: Descargar deshabilitado va sobre superficie oscura. Con
        # el fondo cian su etiqueta gris medía 1.05:1 y no se leía.
        self._pintar_boton(self.boton_descargar, activo=False)
        self.boton_descargar.config(state="disabled")
        self.boton_inspeccionar.config(state="disabled")
        self.boton_cancelar.config(state="normal" if cancelable else "disabled")
        # Arranca indeterminada: si el motor no reporta porcentaje, la barra
        # igual muestra actividad (es la señal de "no está congelada").
        self.barra.config(mode="indeterminate")
        self.barra.start(self.INTERVALO_BARRA_MS)
        self._determinada = False
        # Corrección 3: el texto en curso va en blanco y en negrita, nunca gris.
        self.estado_label.config(fg=T["titular"])
        self.estado_var.set(f"{texto}…")
        self.detalle_label.config(fg=T["texto"])
        self.detalle_var.set("")
        self._escribir_log(f"── {texto} ──", "seccion")

    def _al_mostrar_cancelando(self) -> None:
        """El clic en Cancelar se ve de inmediato: el motor tarda hasta 0,2 s."""
        self.boton_cancelar.config(state="disabled")
        self.estado_label.config(fg=T["titular"])
        self.estado_var.set("Cancelando…")

    def _al_progresar(self, pct: float) -> None:
        if not self._determinada:
            self.barra.stop()
            self.barra.config(mode="determinate")
            self._determinada = True
        self.barra["value"] = pct
        self.estado_label.config(fg=T["titular"])
        self.estado_var.set(f"{self._etiqueta_corrida} — {pct:.0f}%")

    def _al_terminar(self, res: Resultado | None) -> None:
        self.barra.stop()
        self.barra.config(mode="determinate")
        self._determinada = False
        self.barra["value"] = 100.0 if (res is not None and res.ok) else 0.0
        self._pintar_boton(self.boton_descargar, activo=True)
        self.boton_descargar.config(state="normal")
        self.boton_inspeccionar.config(state="normal")
        self.boton_cancelar.config(state="disabled")
        self._cancel = None  # I9: el evento no sobrevive a la corrida
        self.estado = "idle"  # I3: éxito, fallo, cancel o timeout → idle
        if res is None:
            self.estado_label.config(fg=T["muted"])
            self.estado_var.set("Listo.")
            return
        self._mostrar_resultado(res)

    def _pintar_boton(self, boton: tk.Button, activo: bool) -> None:
        """Aplica el par (fondo, texto) de un botón según esté activo o no.

        Regla de diseño: ``inactivo`` (gris) sólo sobre ``elevado`` (5.87:1).
        Sobre el cian el gris mide 1.05:1 y es ilegible, así que el cian se
        reserva para el estado accionable.
        """
        if boton is self.boton_descargar:
            if activo:
                boton.config(bg=T["cian"], fg=T["sobre_cian"])
            else:
                boton.config(bg=T["elevado"], fg=T["inactivo"])
        elif not activo:
            boton.config(fg=T["inactivo"])
        else:
            boton.config(fg=T["texto"])

    def _mostrar_inspeccion(self, datos: dict) -> None:
        plataforma = str(datos.get("plataforma", "?"))
        titulo = str(datos.get("title") or datos.get("id") or "(sin título)")
        duracion = datos.get("duration")
        extra = f" · {_mmss(duracion)}" if isinstance(duracion, (int, float)) else ""
        self.inspeccion_var.set(
            f"{ETIQUETAS_PLATAFORMA.get(plataforma, plataforma)}: {titulo}{extra}"
        )
        self.inspeccion_label.config(fg=T["texto"])
        self._escribir_log(f"Identificado: {self.inspeccion_var.get()}", "ok")

    def _mostrar_resultado(self, res: Resultado) -> None:
        """El espacio de estado: qué pasó, en una línea, y el detalle en otra."""
        self.ultimo = res
        if not res.ok:
            tag, titulo = self._categoria_del_fallo(res.error)
            self.estado_label.config(fg=T[tag])
            self.estado_var.set(titulo)
            self.detalle_label.config(fg=T["texto"])
            self.detalle_var.set(self._detalle_del_fallo(res))
            self._escribir_log(f"{'ERROR' if tag == 'error' else 'AVISO'}: {res.error}", tag)
            self.codec_label.config(fg=T["muted"])
            self.codec_var.set("Sin archivo: no se midió ningún códec.")
            return

        nombre = res.archivo.name if res.archivo is not None else "el archivo"
        self.estado_label.config(fg=T["ok"])
        self.estado_var.set(f"Descarga finalizada: {nombre}")
        self._escribir_log(f"Listo: {res.archivo}", "ok")
        texto, tag = self._medir_codec(res.archivo)
        self.codec_var.set(texto)
        self.codec_label.config(fg=T[tag])
        notas: list[str] = []
        if res.aviso:
            notas.append(res.aviso)
            self._escribir_log(f"AVISO: {res.aviso}", "aviso")
        # D13: informar en vez de borrar. El original se nombra, no se toca.
        if res.original is not None and "Se eliminó el original" not in (res.aviso or ""):
            notas.append(
                f"Original conservado en {res.original.parent.name}: "
                f"{res.original.name} — podés borrarlo a mano."
            )
        self.detalle_label.config(fg=T["texto"])
        self.detalle_var.set(" ".join(notas))

    def _categoria_del_fallo(self, error: str | None) -> tuple[str, str]:
        """``(tag_de_color, texto_del_estado)`` de un fallo. Nada de genéricos."""
        mensaje = (error or "Fallo sin detalle del motor.").strip()
        if mensaje == MENSAJE_CANCELADO:
            # Cancelar es una acción del usuario, no una falla: va en blanco.
            return "texto", mensaje
        if "tiempo de espera" in mensaje:
            return "aviso", mensaje
        return "error", f"Error: {mensaje}"

    def _detalle_del_fallo(self, res: Resultado) -> str:
        """Segunda línea del espacio de estado cuando algo salió mal."""
        notas: list[str] = []
        if "tiempo de espera" in (res.error or ""):
            notas.append(HINT_TIMEOUT_UI)
        if res.aviso:
            notas.append(res.aviso)
        notas.append(f"Detalle de yt-dlp: {self.ruta_log.name} (botón Ver registro).")
        return " ".join(notas)

    def _medir_codec(self, archivo) -> tuple[str, str]:
        """Mide el códec real con ffprobe. Devuelve ``(texto, tag_de_color)``."""
        if archivo is None:
            return "Sin archivo: no se midió ningún códec.", "muted"
        try:
            cantidad, vcodec, acodec = streams_de(archivo)
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            return f"No se pudo medir el códec: {exc}", "error"
        piezas = [c for c in (vcodec, acodec) if c]
        detalle = " + ".join(piezas) if piezas else "sin códec detectado"
        texto = f"{cantidad} stream(s): {detalle}"
        # Video pedido y un solo stream ⇒ "SIN audio": ámbar, nunca verde (D9).
        if self._opciones_actuales().modo == "video" and cantidad < 2:
            return f"{texto} — SIN audio", "aviso"
        tag = "ok" if (vcodec == "h264" or self._opciones_actuales().modo == "audio") else "aviso"
        return texto, tag

    def _opciones_actuales(self) -> Opciones:
        """Opción de calidad seleccionada (sólo lectura de widgets)."""
        elegida = self.calidad_var.get()
        for calidad in CALIDADES:
            if calidad.etiqueta == elegida:
                return calidad.opciones
        return CALIDADES[0].opciones

    def _escribir_log(self, texto: str, tag: str = "cuerpo") -> None:
        """Guarda una línea en el detalle de la corrida y en el archivo.

        Ya no hay caja de registro en pantalla: el detalle se alcanza por
        *tooltip* y por archivo, pero **no** se pierde.
        """
        self.detalle.append(texto)
        try:
            with open(self.ruta_log, "a", encoding="utf-8", errors="replace") as archivo:
                archivo.write(texto + "\n")
        except OSError:
            pass  # el registro es un diagnóstico: nunca puede romper la interfaz

    def _texto_detalle(self) -> str:
        """Contenido del tooltip: últimas líneas del motor y ruta del archivo."""
        lineas = list(self.detalle)[-TooltipDetalle.LINEAS:]
        if not lineas:
            return ""
        cuerpo = "\n".join(lineas)
        return f"Últimas líneas de yt-dlp:\n{cuerpo}\n\nRegistro completo: {self.ruta_log}"

    # ------------------------------------------------------------ construcción

    def _construir(self) -> None:
        root = self.root
        root.title("Descargador de redes")
        root.configure(bg=T["app"])
        # Medido con ``winfo_reqheight()`` tras construir: 650 px sin la caja
        # de registro (antes eran 840). El alto se deja holgado y el mínimo
        # permite achicar sin recortar el contenido esencial.
        root.geometry("780x700")
        root.minsize(660, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        marco = tk.Frame(root, bg=T["app"])
        marco.grid(row=0, column=0, sticky="nsew", padx=12, pady=10)
        marco.columnconfigure(0, weight=1)

        self._construir_encabezado(marco)
        self._construir_link(marco)
        self._construir_calidad(marco)
        self._construir_cookies(marco)
        self._construir_accion(marco)
        self._construir_pie(marco)

        # Orden de tabulación = orden de creación: link → inspeccionar →
        # calidad → cookies → descargar → cancelar → abrir carpeta → registro.
        # Tk deja los botones y los radios fuera del recorrido si no se pide,
        # así que todo lo interactivo lleva takefocus=1 explícito.
        self.entry_url.focus_set()

    def _seccion(self, padre: tk.Misc, titulo: str, fila: int) -> tk.Frame:
        panel = tk.Frame(padre, bg=T["panel"], highlightbackground=T["borde_sutil"], highlightthickness=1)
        panel.grid(row=fila, column=0, sticky="ew", padx=0, pady=(0, 8))
        panel.columnconfigure(1, weight=1)
        tk.Label(
            panel,
            text=titulo,
            bg=T["panel"],
            fg=T["titular"],
            font=FUENTE_SECCION,
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        return panel

    def _radio_bloque(self, padre: tk.Misc, texto: str, variable: tk.StringVar, valor: str) -> tk.Radiobutton:
        """Radio **en bloque** (sin indicador redondo): el estado se ve entero.

        ``indicatoron=0`` convierte el radio en un bloque: el seleccionado se
        hunde y se rellena. El color va en ``bg`` **y** en ``selectcolor``
        porque según la versión de Tk uno u otro manda; poner los dos hace que
        el resultado no dependa de cuál gane.
        """
        return tk.Radiobutton(
            padre,
            text=texto,
            variable=variable,
            value=valor,
            indicatoron=0,
            bg=T["elevado"],
            fg=T["texto"],
            selectcolor=T["elevado"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            disabledforeground=T["inactivo"],
            font=FUENTE_UI,
            anchor="w",
            padx=10,
            pady=5,
            relief="flat",
            highlightthickness=2,
            highlightbackground=T["panel"],
            highlightcolor=T["cian_hover"],
            takefocus=1,
        )

    def _pintar_grupo(
        self, radios: list[tuple[tk.Radiobutton, object]], variable: tk.StringVar
    ) -> None:
        """Repinta un grupo de radios: el elegido se ve de un vistazo.

        Tres señales simultáneas y ninguna es sólo el color: glifo
        (``●``/``○``), peso de la fuente y relleno del bloque.
        """
        elegida = variable.get()
        for boton, _valor in radios:
            base = getattr(boton, "_texto_base", "")
            marcado = str(boton.cget("value")) == str(elegida)
            if marcado:
                boton.config(
                    text=f"{GLIFO_MARCADO}  {base}",
                    bg=T["cian"],
                    selectcolor=T["cian"],
                    fg=T["sobre_cian"],
                    activebackground=T["cian_hover"],
                    activeforeground=T["sobre_cian"],
                    font=FUENTE_UI_BOLD,
                    relief="sunken",
                )
            else:
                boton.config(
                    text=f"{GLIFO_VACIO}  {base}",
                    bg=T["elevado"],
                    selectcolor=T["elevado"],
                    fg=T["texto"],
                    activebackground=T["hover"],
                    activeforeground=T["texto"],
                    font=FUENTE_UI,
                    relief="flat",
                )

    def _construir_encabezado(self, padre: tk.Misc) -> None:
        encabezado = tk.Frame(padre, bg=T["app"])
        encabezado.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        encabezado.columnconfigure(0, weight=1)
        tk.Label(
            encabezado,
            text="Descargador de redes",
            bg=T["app"],
            fg=T["titular"],
            font=FUENTE_TITULAR,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            encabezado,
            text="YouTube · Instagram · TikTok — uso personal",
            bg=T["app"],
            fg=T["muted"],
            font=FUENTE_UI,
            anchor="w",
        ).grid(row=1, column=0, sticky="w")
        # La versión del motor a la vista: es el detector de deriva del usuario.
        tk.Label(
            encabezado,
            text=f"yt-dlp {self._version}",
            bg=T["app"],
            fg=T["muted"],
            font=FUENTE_LOG,
            anchor="e",
        ).grid(row=0, column=1, rowspan=2, sticky="e")

    def _construir_link(self, padre: tk.Misc) -> None:
        panel = self._seccion(padre, "1. Pegá el link", 1)

        self.url_var = tk.StringVar(value="")
        self.inspeccion_var = tk.StringVar(value="Todavía no se inspeccionó ningún link.")

        tk.Label(panel, text="Link", bg=T["panel"], fg=T["muted"], font=FUENTE_UI).grid(
            row=1, column=0, sticky="w", padx=(10, 6)
        )
        self.entry_url = tk.Entry(
            panel,
            textvariable=self.url_var,
            bg=T["elevado"],
            fg=T["texto"],
            insertbackground=T["texto"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            font=FUENTE_UI,
            disabledbackground=T["elevado"],
            disabledforeground=T["inactivo"],
        )
        self.entry_url.grid(row=1, column=1, sticky="ew", ipady=4, pady=2)

        self.boton_inspeccionar = tk.Button(
            panel,
            text="Inspeccionar",
            command=self._al_inspeccionar,
            bg=T["elevado"],
            fg=T["texto"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            disabledforeground=T["inactivo"],
            relief="flat",
            font=FUENTE_UI_BOLD,
            padx=14,
            pady=4,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            takefocus=1,
        )
        self.boton_inspeccionar.grid(row=1, column=2, sticky="ew", padx=(8, 10))

        self.inspeccion_label = tk.Label(
            panel,
            textvariable=self.inspeccion_var,
            bg=T["panel"],
            fg=T["muted"],
            font=FUENTE_UI,
            anchor="w",
            justify="left",
            wraplength=680,
        )
        self.inspeccion_label.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

    def _construir_calidad(self, padre: tk.Misc) -> None:
        panel = self._seccion(padre, "2. Elegí qué bajar", 2)
        self.calidad_var = tk.StringVar(value=CALIDAD_POR_DEFECTO)

        # Radios en bloque y en dos columnas: las cuatro opciones quedan
        # visibles a la vez, sin estado oculto detrás de un desplegable.
        for indice, calidad in enumerate(CALIDADES):
            fila, columna = divmod(indice, 2)
            texto = f"{calidad.etiqueta}  ({calidad.detalle})"
            radio = self._radio_bloque(panel, texto, self.calidad_var, calidad.etiqueta)
            radio._texto_base = texto  # el glifo se agrega al pintar, no al texto base
            radio.grid(row=1 + fila, column=columna, sticky="ew", padx=(10, 4), pady=(2, 2))
            self._radios_calidad.append((radio, calidad))

        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        tk.Frame(panel, bg=T["panel"], height=6).grid(row=3, column=0, columnspan=2)

        # Repintar ante cualquier cambio —incluido el que llega por teclado—.
        self.calidad_var.trace_add("write", lambda *_: self._pintar_calidad())
        self._pintar_calidad()

    def _pintar_calidad(self) -> None:
        self._pintar_grupo(self._radios_calidad, self.calidad_var)

    def _construir_cookies(self, padre: tk.Misc) -> None:
        panel = self._seccion(padre, "3. Cookies", 3)
        panel.columnconfigure(1, weight=1)
        self.cookies_var = tk.StringVar(value=SIN_COOKIES)
        self.ruta_cookies_var = tk.StringVar(value="")

        opciones = (
            (SIN_COOKIES, "Sin cookies  (recomendado: la descarga es anónima)"),
            (CON_ARCHIVO, "Archivo de cookies (Netscape)"),
        )
        for fila, (valor, texto) in enumerate(opciones, start=1):
            radio = self._radio_bloque(panel, texto, self.cookies_var, valor)
            radio._texto_base = texto
            radio.grid(row=fila, column=0, sticky="ew", padx=(10, 8), pady=(2, 2))
            self._radios_cookies.append((radio, valor))

        self.entry_cookies = tk.Entry(
            panel,
            textvariable=self.ruta_cookies_var,
            bg=T["elevado"],
            fg=T["texto"],
            insertbackground=T["texto"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            font=FUENTE_UI,
        )
        self.entry_cookies.grid(row=2, column=1, sticky="ew", padx=8, ipady=3)

        tk.Button(
            panel,
            text="Elegir archivo…",
            command=self._al_elegir_cookies,
            bg=T["elevado"],
            fg=T["texto"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            relief="flat",
            font=FUENTE_UI,
            padx=8,
            pady=2,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            takefocus=1,
        ).grid(row=2, column=2, sticky="ew", padx=(0, 10))

        tk.Label(
            panel,
            text=NOTA_COOKIES,
            bg=T["panel"],
            fg=T["muted"],
            font=FUENTE_UI,
            anchor="w",
            justify="left",
            wraplength=700,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

        self.cookies_var.trace_add("write", lambda *_: self._pintar_cookies())
        self._pintar_cookies()

    def _pintar_cookies(self) -> None:
        self._pintar_grupo(self._radios_cookies, self.cookies_var)

    def _construir_accion(self, padre: tk.Misc) -> None:
        panel = tk.Frame(padre, bg=T["app"])
        panel.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        panel.columnconfigure(2, weight=1)

        self.boton_descargar = tk.Button(
            panel,
            text="Descargar",
            command=self._al_descargar,
            bg=T["cian"],
            fg=T["sobre_cian"],
            activebackground=T["cian_hover"],
            activeforeground=T["sobre_cian"],
            disabledforeground=T["inactivo"],
            relief="flat",
            font=FUENTE_BOTON,
            padx=26,
            pady=8,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian_hover"],
            takefocus=1,
        )
        self.boton_descargar.grid(row=0, column=0, sticky="w")

        # D12: sólo vive mientras corre una descarga. En idle está apagado.
        self.boton_cancelar = tk.Button(
            panel,
            text="Cancelar",
            command=self._al_cancelar,
            bg=T["elevado"],
            fg=T["texto"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            disabledforeground=T["inactivo"],
            relief="flat",
            font=FUENTE_BOTON,
            padx=18,
            pady=8,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian_hover"],
            takefocus=1,
            state="disabled",
        )
        self.boton_cancelar.grid(row=0, column=1, sticky="w", padx=(8, 12))

        estilo = ttk.Style()
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure(
            "Descarga.Horizontal.TProgressbar",
            troughcolor=T["elevado"],
            background=T["cian"],
            bordercolor=T["borde"],
            lightcolor=T["cian"],
            darkcolor=T["cian"],
        )
        self.barra = ttk.Progressbar(
            panel,
            style="Descarga.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
        )
        self.barra.grid(row=0, column=2, sticky="ew")

        # Corrección 2: el espacio de estado reemplaza a la caja de registro.
        # Dos líneas, sin recuadro: qué pasó y, si hace falta, el detalle.
        self.estado_var = tk.StringVar(value="Esperando un link.")
        self.estado_label = tk.Label(
            panel,
            textvariable=self.estado_var,
            bg=T["app"],
            fg=T["muted"],
            font=FUENTE_ESTADO,
            anchor="w",
            justify="left",
            wraplength=740,
        )
        self.estado_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.detalle_var = tk.StringVar(value="")
        self.detalle_label = tk.Label(
            panel,
            textvariable=self.detalle_var,
            bg=T["app"],
            fg=T["texto"],
            font=FUENTE_DETALLE,
            anchor="w",
            justify="left",
            wraplength=740,
        )
        self.detalle_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

        TooltipDetalle(self.estado_label, self._texto_detalle)
        TooltipDetalle(self.detalle_label, self._texto_detalle)

    def _construir_pie(self, padre: tk.Misc) -> None:
        pie = tk.Frame(padre, bg=T["app"])
        pie.grid(row=5, column=0, sticky="ew")
        pie.columnconfigure(2, weight=1)

        tk.Button(
            pie,
            text="Abrir carpeta",
            command=self._al_abrir_carpeta,
            bg=T["elevado"],
            fg=T["texto"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            relief="flat",
            font=FUENTE_UI_BOLD,
            padx=12,
            pady=4,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            takefocus=1,
        ).grid(row=0, column=0, sticky="w")

        tk.Button(
            pie,
            text="Ver registro",
            command=self._al_ver_registro,
            bg=T["elevado"],
            fg=T["texto"],
            activebackground=T["hover"],
            activeforeground=T["texto"],
            relief="flat",
            font=FUENTE_UI_BOLD,
            padx=12,
            pady=4,
            highlightthickness=2,
            highlightbackground=T["borde"],
            highlightcolor=T["cian"],
            takefocus=1,
        ).grid(row=0, column=1, sticky="w", padx=(8, 12))

        self.codec_var = tk.StringVar(value="Todavía no se midió ningún códec.")
        self.codec_label = tk.Label(
            pie,
            textvariable=self.codec_var,
            bg=T["app"],
            fg=T["muted"],
            font=FUENTE_UI,
            anchor="w",
            justify="left",
            wraplength=520,
        )
        self.codec_label.grid(row=0, column=2, sticky="w")

        tk.Label(
            pie,
            text=AVISO_LIMITACIONES,
            bg=T["app"],
            fg=T["aviso"],
            font=FUENTE_UI,
            anchor="w",
            justify="left",
            wraplength=740,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # --------------------------------------------------------------- manejadores
    # Los manejadores sólo LEEN widgets y encolan: la escritura vive en _aplicar.

    def _al_inspeccionar(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.cola.put(("log", ("Pegá un link antes de inspeccionar.", "aviso")))
            return
        self.inspeccionar(url)

    def _al_descargar(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.cola.put(("log", ("Pegá un link antes de descargar.", "aviso")))
            return
        self.iniciar(url, self._opciones_actuales())

    def _al_cancelar(self) -> None:
        """D12: prende el evento que el núcleo ya está sondeando."""
        if self._cancel is None or self.estado != "ocupado":
            return
        self._cancel.set()
        self.cola.put(("cancelando", None))

    def _al_elegir_cookies(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Elegí el archivo de cookies (formato Netscape)",
            filetypes=(("Archivo de cookies", "*.txt"), ("Todos los archivos", "*.*")),
        )
        if ruta:
            self.ruta_cookies_var.set(ruta)
            self.cookies_var.set(CON_ARCHIVO)

    def _al_abrir_carpeta(self) -> None:
        salida = resolver_salida(self.cfg)
        try:
            salida.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.cola.put(("log", (f"No se pudo crear la carpeta de salida: {exc}", "error")))
            return
        try:
            os.startfile(str(salida))  # type: ignore[attr-defined]  # Windows
        except (AttributeError, OSError) as exc:
            self.cola.put(("log", (f"No se pudo abrir la carpeta: {exc}", "error")))

    def _al_ver_registro(self) -> None:
        """El detalle del motor sigue siendo alcanzable: se abre el archivo."""
        if not self.ruta_log.exists():
            self.detalle_label.config(fg=T["texto"])
            self.detalle_var.set(
                "Todavía no hay registro: se crea con la primera descarga."
            )
            return
        try:
            os.startfile(str(self.ruta_log))  # type: ignore[attr-defined]  # Windows
        except (AttributeError, OSError) as exc:
            self.detalle_label.config(fg=T["error"])
            self.detalle_var.set(f"No se pudo abrir el registro: {exc}")
