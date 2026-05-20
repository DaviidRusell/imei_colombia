from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from typing import Optional
import re
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# --- Modelo ---

class IMEIResponse(BaseModel):
    imei: str
    estado: str
    en_base_negativa: Optional[bool]
    operador: Optional[str]
    mensaje: str

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
        return IMEIResponse(imei=imei_resp, estado="LIMPIO", en_base_negativa=False, operador=None, mensaje=mensaje)
    elif "duplicado" in msg_lower:
        return IMEIResponse(imei=imei_resp, estado="DUPLICADO", en_base_negativa=True, operador=mensaje, mensaje=mensaje)
    elif "reportado" in msg_lower or ("base de datos negativa" in msg_lower and "no se encuentra" not in msg_lower):
        return IMEIResponse(imei=imei_resp, estado="REPORTADO", en_base_negativa=True, operador=mensaje, mensaje=mensaje)
    elif "inválido" in msg_lower or "invalido" in msg_lower:
        return IMEIResponse(imei=imei_resp, estado="INVALIDO", en_base_negativa=False, operador=None, mensaje=mensaje)
    elif "no registrado" in msg_lower:
        return IMEIResponse(imei=imei_resp, estado="NO_REGISTRADO", en_base_negativa=False, operador=None, mensaje=mensaje)
    else:
        return IMEIResponse(imei=imei_resp, estado="DESCONOCIDO", en_base_negativa=None, operador=None, mensaje=mensaje)

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
        raise HTTPException(status_code=502, detail=f"El servidor SRTM respondió con HTTP {response.status_code}")

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

@app.get(
    "/health",
    summary="Estado del servicio",
    tags=["Sistema"]
)
def health():
    return {"status": "ok", "version": "1.0.0", "fuente": "imeicolombia.com.co (SRTM)"}