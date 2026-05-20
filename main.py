from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
import requests
from bs4 import BeautifulSoup
import time
import asyncio
from typing import Optional
import re

app = FastAPI(
    title="API IMEI Colombia",
    description="Consulta el estado de equipos móviles en la Base de Datos Negativa de Colombia (SRTM)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Modelos ---

class IMEIResponse(BaseModel):
    imei: str
    estado: str
    en_base_negativa: Optional[bool]
    operador: Optional[str]
    mensaje: str

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
    total: int
    resultados: list[IMEIResponse]
    errores: int

# --- Lógica de consulta ---

def validar_imei(imei: str) -> bool:
    return bool(re.match(r'^\d{15}$', imei))

def parsear_respuesta(imei: str, html: str) -> IMEIResponse:
    soup = BeautifulSoup(html, 'html.parser')

    fila_datos = soup.find('tr', class_='azlc')

    if not fila_datos:
        return IMEIResponse(
            imei=imei,
            estado="ERROR",
            en_base_negativa=None,
            operador=None,
            mensaje="No se encontró resultado en la respuesta del servidor"
        )

    celdas = fila_datos.find_all('td')

    if len(celdas) < 2:
        return IMEIResponse(
            imei=imei,
            estado="ERROR",
            en_base_negativa=None,
            operador=None,
            mensaje="Estructura de respuesta inesperada"
        )

    imei_resp = celdas[0].get_text(strip=True)
    mensaje   = celdas[1].get_text(strip=True)
    msg_lower = mensaje.lower()

    if "no se encuentra registrado en la base de datos negativa" in msg_lower:
        return IMEIResponse(
            imei=imei_resp, estado="LIMPIO",
            en_base_negativa=False, operador=None, mensaje=mensaje
        )
    elif "duplicado" in msg_lower:
        return IMEIResponse(
            imei=imei_resp, estado="DUPLICADO",
            en_base_negativa=True, operador=mensaje, mensaje=mensaje
        )
    elif "reportado" in msg_lower or ("base de datos negativa" in msg_lower and "no se encuentra" not in msg_lower):
        return IMEIResponse(
            imei=imei_resp, estado="REPORTADO",
            en_base_negativa=True, operador=mensaje, mensaje=mensaje
        )
    elif "inválido" in msg_lower or "invalido" in msg_lower:
        return IMEIResponse(
            imei=imei_resp, estado="INVALIDO",
            en_base_negativa=False, operador=None, mensaje=mensaje
        )
    elif "no registrado" in msg_lower:
        return IMEIResponse(
            imei=imei_resp, estado="NO_REGISTRADO",
            en_base_negativa=False, operador=None, mensaje=mensaje
        )
    else:
        return IMEIResponse(
            imei=imei_resp, estado="DESCONOCIDO",
            en_base_negativa=None, operador=None, mensaje=mensaje
        )

def consultar_imei_srtm(imei: str) -> IMEIResponse:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.imeicolombia.com.co/",
        "Origin": "https://www.imeicolombia.com.co"
    }

    session = requests.Session()
    
    # ✅ Agregar verify=False en ambas llamadas
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    session.get("https://www.imeicolombia.com.co/", headers=headers, timeout=10, verify=False)

    response = session.post(
        "https://www.imeicolombia.com.co/Consulta",
        data={"IMEI": imei},
        headers=headers,
        timeout=15,
        verify=False   # ✅ también aquí
    )
    response.encoding = 'iso-8859-1'

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"El servidor SRTM respondió con HTTP {response.status_code}"
        )

    return parsear_respuesta(imei, response.text)

# --- Endpoints ---

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
    Consulta el estado de un IMEI en la Base de Datos Negativa de Colombia (SRTM).

    - **LIMPIO**: El equipo no está reportado.
    - **REPORTADO**: El equipo está bloqueado (robo, pérdida, etc.).
    - **DUPLICADO**: El IMEI aparece en más de un equipo.
    - **INVALIDO**: El IMEI no tiene aprobación GSMA/CRC.
    - **NO_REGISTRADO**: El equipo no está en la base positiva.
    """
    if not validar_imei(imei):
        raise HTTPException(status_code=400, detail="IMEI inválido. Debe tener exactamente 15 dígitos numéricos.")

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
    Se aplica una pausa de 1.5s entre consultas para no saturar el servidor SRTM.
    """
    resultados = []
    errores = 0

    for i, imei in enumerate(body.imeis):
        if not validar_imei(imei):
            resultados.append(IMEIResponse(
                imei=imei, estado="ERROR",
                en_base_negativa=None, operador=None,
                mensaje="IMEI inválido: debe tener 15 dígitos numéricos"
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
                en_base_negativa=None, operador=None,
                mensaje=str(e)
            ))
            errores += 1

        if i < len(body.imeis) - 1:
            time.sleep(1.5)

    return BatchResponse(
        total=len(resultados),
        resultados=resultados,
        errores=errores
    )

@app.get(
    "/health",
    summary="Estado del servicio",
    tags=["Sistema"]
)
def health():
    return {"status": "ok", "version": "1.0.0", "fuente": "imeicolombia.com.co (SRTM)"}
