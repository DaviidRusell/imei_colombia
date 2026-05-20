# API IMEI Colombia

Consulta el estado de equipos móviles en la Base de Datos Negativa del SRTM (imeicolombia.com.co).

## Instalación

```bash
pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn main:app --reload --port 8000
```

Abre http://localhost:8000 para la interfaz web.
Documentación Swagger en http://localhost:8000/docs

## Endpoints

### GET /imei/{imei}
Consulta individual.

```bash
curl http://localhost:8000/imei/353265110903640
```

Respuesta:
```json
{
  "imei": "353265110903640",
  "estado": "LIMPIO",
  "en_base_negativa": false,
  "operador": null,
  "mensaje": "El IMEI no se encuentra registrado en la Base de Datos Negativa"
}
```

### POST /imei/batch
Consulta hasta 20 IMEIs.

```bash
curl -X POST http://localhost:8000/imei/batch \
  -H "Content-Type: application/json" \
  -d '{"imeis": ["353265110903640", "490154203237518"]}'
```

### GET /health
Estado del servicio.

## Estados posibles

| Estado          | Descripción                                      |
|-----------------|--------------------------------------------------|
| LIMPIO          | Equipo sin reportes, puede usarse normalmente    |
| REPORTADO       | Equipo bloqueado (robo, pérdida, impago, etc.)   |
| DUPLICADO       | IMEI aparece en más de un equipo                 |
| INVALIDO        | Sin aprobación GSMA/CRC                          |
| NO_REGISTRADO   | No está en la base de datos positiva             |
| ERROR           | Error en la consulta                             |

## Aviso Legal

Este servicio consulta información pública del SRTM (Sistema de Registro de
Terminal Móvil) administrado por Inetum bajo el marco regulatorio de la CRC.
Úselo para fines legítimos: verificación de equipos propios, compras de segunda
mano o sistemas de gestión autorizados.
