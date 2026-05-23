from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
import requests
import urllib3
from bs4 import BeautifulSoup
import time
from typing import Optional
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(
    title="API IMEI Colombia",
    description="Consulta el estado de equipos móviles en la Base de Datos Negativa de Colombia (SRTM)",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Modelos ──────────────────────────────────────────────────────────────────

class Reporte(BaseModel):
    """Un reporte individual: una causal + un operador."""
    causal:   str            # ROBO_HURTO | BLOQUEO_NO_REGISTRADO | DUPLICADO | REINCIDENTE | etc.
    causal_texto: str        # Texto original del HTML
    operador: str            # Nombre del operador que reportó

class IMEIResponse(BaseModel):
    imei:             str
    estado:           str            # LIMPIO | REPORTADO | MULTIPLE | ERROR
    en_base_negativa: bool
    causales:         list[str]      # Lista de causales únicas presentes
    operadores:       list[str]      # Lista de operadores únicos
    reportes:         list[Reporte]  # Detalle completo fila por fila
    total_reportes:   int
    resumen:          str            # Mensaje legible para el usuario

class BatchRequest(BaseModel):
    imeis: list[str]

    @field_validator("imeis")
    @classmethod
    def validar_lista(cls, v):
        if len(v) == 0:
            raise ValueError("La lista de IMEIs no puede estar vacía")
        if len(v) > 20:
            raise ValueError("Máximo 20 IMEIs por solicitud")
        return v

class BatchResponse(BaseModel):
    total:      int
    resultados: list[IMEIResponse]
    errores:    int

# ─── Clasificador de causales ─────────────────────────────────────────────────

# Mapa de palabras clave → código de causal normalizado
CAUSALES_MAP = {
    "robo/hurto":           "ROBO_HURTO",
    "robo":                 "ROBO_HURTO",
    "hurto":                "ROBO_HURTO",
    "bloqueo/no registrado":"BLOQUEO_NO_REGISTRADO",
    "bloqueo":              "BLOQUEO_NO_REGISTRADO",
    "no registrado":        "BLOQUEO_NO_REGISTRADO",
    "duplicado":            "DUPLICADO",
    "reincidente":          "REINCIDENTE",
    "pérdida":              "PERDIDA",
    "perdida":              "PERDIDA",
    "prepago":              "PREPAGO",
    "postpago":             "POSTPAGO",
}

def clasificar_causal(texto: str) -> str:
    """Detecta la causal del texto en negrita dentro del mensaje."""
    t = texto.lower()
    for clave, codigo in CAUSALES_MAP.items():
        if clave in t:
            return codigo
    return "OTRO"

def extraer_causal_texto(celda) -> str:
    """Extrae el texto en <b> de la celda (ej: 'Robo/Hurto')."""
    bold = celda.find('b')
    if bold:
        texto = bold.get_text(strip=True)
        # Quitar el IMEI que viene pegado: "Robo/Hurto: 353637383940410"
        texto = re.sub(r':\s*\d+', '', texto).strip()
        return texto
    return celda.get_text(strip=True)

# ─── Parser principal ─────────────────────────────────────────────────────────

def parsear_respuesta(imei: str, html: str) -> IMEIResponse:
    soup = BeautifulSoup(html, 'html.parser')
    filas = soup.find_all('tr', class_='azlc')

    if not filas:
        return IMEIResponse(
            imei=imei,
            estado="ERROR",
            en_base_negativa=False,
            causales=[],
            operadores=[],
            reportes=[],
            total_reportes=0,
            resumen="No se encontró resultado en la respuesta del servidor"
        )

    reportes: list[Reporte] = []

    for fila in filas:
        celdas = fila.find_all('td')
        if len(celdas) < 2:
            continue

        celda_msg      = celdas[0]
        celda_operador = celdas[1]

        msg_texto  = celda_msg.get_text(strip=True)
        operador   = celda_operador.get_text(strip=True)
        msg_lower  = msg_texto.lower()

        # ── Caso LIMPIO: el IMEI está en la primera celda y el mensaje en la segunda ──
        # Estructura: <td>353265110903640</td><td>El IMEI no se encuentra registrado...</td>
        if "no se encuentra registrado en la base de datos negativa" in celda_operador.get_text(strip=True).lower():
            return IMEIResponse(
                imei=msg_texto,  # primera celda es el IMEI
                estado="LIMPIO",
                en_base_negativa=False,
                causales=[],
                operadores=[],
                reportes=[],
                total_reportes=0,
                resumen="El IMEI no se encuentra registrado en la Base de Datos Negativa"
            )

        causal_texto = extraer_causal_texto(celda_msg)
        causal_cod   = clasificar_causal(msg_lower)

        reportes.append(Reporte(
            causal=causal_cod,
            causal_texto=causal_texto,
            operador=operador,
        ))

    if not reportes:
        return IMEIResponse(
            imei=imei,
            estado="DESCONOCIDO",
            en_base_negativa=False,
            causales=[],
            operadores=[],
            reportes=[],
            total_reportes=0,
            resumen="No se pudo interpretar la respuesta del SRTM"
        )

    # Causales y operadores únicos (manteniendo orden de aparición)
    causales_unicas  = list(dict.fromkeys(r.causal   for r in reportes))
    operadores_unicos = list(dict.fromkeys(r.operador for r in reportes))

    # Estado general
    if len(reportes) == 1:
        estado = reportes[0].causal
    else:
        estado = "MULTIPLE"

    # Resumen legible
    causales_str   = ", ".join(causales_unicas)
    operadores_str = ", ".join(operadores_unicos)
    resumen = (
        f"Reportado por {len(reportes)} causal(es): {causales_str}. "
        f"Operador(es): {operadores_str}."
    )

    return IMEIResponse(
        imei=imei,
        estado=estado,
        en_base_negativa=True,
        causales=causales_unicas,
        operadores=operadores_unicos,
        reportes=reportes,
        total_reportes=len(reportes),
        resumen=resumen
    )

# ─── HTTP al SRTM ─────────────────────────────────────────────────────────────

def consultar_imei_srtm(imei: str) -> IMEIResponse:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.imeicolombia.com.co/",
        "Origin": "https://www.imeicolombia.com.co"
    }

    session = requests.Session()
    session.get("https://www.imeicolombia.com.co/", headers=headers, timeout=10, verify=False)

    response = session.post(
        "https://www.imeicolombia.com.co/Consulta",
        data={"IMEI": imei},
        headers=headers,
        timeout=15,
        verify=False
    )
    response.encoding = 'iso-8859-1'

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"El servidor SRTM respondió con HTTP {response.status_code}"
        )

    return parsear_respuesta(imei, response.text)

def validar_imei(imei: str) -> bool:
    return bool(re.match(r'^\d{15}$', imei))

# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get(
    "/imei/{imei}",
    response_model=IMEIResponse,
    summary="Consultar un IMEI",
    tags=["IMEI"]
)
def consultar_imei(imei: str):
    """
    Consulta el estado de un IMEI en la Base de Datos Negativa del SRTM Colombia.

    **Estados posibles:**
    - `LIMPIO` — No está en la base negativa
    - `ROBO_HURTO` — Reportado por robo o hurto
    - `BLOQUEO_NO_REGISTRADO` — Bloqueado / no registrado
    - `DUPLICADO` — IMEI duplicado
    - `REINCIDENTE` — Reincidente
    - `PERDIDA` — Reportado por pérdida
    - `MULTIPLE` — Tiene más de un tipo de reporte
    - `OTRO` — Causal no reconocida
    - `ERROR` — Error en la consulta

    Cuando hay **múltiples reportes**, el campo `reportes[]` contiene el detalle
    de cada fila con su causal y operador.
    """
    if not validar_imei(imei):
        raise HTTPException(
            status_code=400,
            detail="IMEI inválido. Debe tener exactamente 15 dígitos numéricos."
        )
    return consultar_imei_srtm(imei)

@app.post(
    "/imei/batch",
    response_model=BatchResponse,
    summary="Consultar múltiples IMEIs",
    tags=["IMEI"]
)
def consultar_batch(body: BatchRequest):
    """
    Consulta hasta **20 IMEIs** en una sola petición.
    Pausa de 1.5s entre consultas para no saturar el SRTM.
    """
    resultados = []
    errores    = 0

    for i, imei in enumerate(body.imeis):
        if not validar_imei(imei):
            resultados.append(IMEIResponse(
                imei=imei, estado="ERROR",
                en_base_negativa=False,
                causales=[], operadores=[], reportes=[],
                total_reportes=0,
                resumen="IMEI inválido: debe tener 15 dígitos numéricos"
            ))
            errores += 1
            continue

        try:
            resultado = consultar_imei_srtm(imei)
            resultados.append(resultado)
            if resultado.estado == "ERROR":
                errores += 1
        except Exception as e:
            resultados.append(IMEIResponse(
                imei=imei, estado="ERROR",
                en_base_negativa=False,
                causales=[], operadores=[], reportes=[],
                total_reportes=0,
                resumen=str(e)
            ))
            errores += 1

        if i < len(body.imeis) - 1:
            time.sleep(1.5)

    return BatchResponse(
        total=len(resultados),
        resultados=resultados,
        errores=errores
    )

@app.get("/health", summary="Estado del servicio", tags=["Sistema"])
def health():
    return {"status": "ok", "version": "2.0.0", "fuente": "imeicolombia.com.co (SRTM)"}