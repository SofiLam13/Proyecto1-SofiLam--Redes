"""
Asistente en consola que:
  1) CREA eventos en Google Calendar desde lenguaje natural en español.
  2) LISTA tu agenda: "qué debo hacer hoy?", "qué tareas tengo para mañana?", "ver mi agenda de esta semana".

Zona horaria por defecto: America/Guatemala
"""

import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import dateparser
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ============================ CONFIG ============================
TZ = pytz.timezone("America/Guatemala")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DEFAULT_DURATION_MIN = 60

# Email opcional (SMTP Gmail)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

def _read_txt(name: str) -> Optional[str]:
    try:
        with open(name, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or _read_txt("SENDER_EMAIL.txt")
SENDER_PASS  = os.environ.get("SENDER_PASS") or _read_txt("SENDER_PASS.txt")
DEFAULT_NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL") or _read_txt("NOTIFY_EMAIL.txt")
# ===============================================================

@dataclass
class PendingEvent:
    title: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    location: Optional[str] = None
    description: Optional[str] = None
    duration_min: Optional[int] = None

# ================================================================
# Google Calendar helpers
# ================================================================

def get_calendar_service() -> Any:
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "No encuentro credentials.json. Descárgalo de Google Cloud Console y colócalo junto al script.")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    service = build("calendar", "v3", credentials=creds)
    return service


def create_event(service, ev: PendingEvent) -> Dict[str, Any]:
    start_dt = ev.start.astimezone(TZ)
    if ev.end:
        end_dt = ev.end.astimezone(TZ)
    else:
        dur = ev.duration_min or DEFAULT_DURATION_MIN
        end_dt = start_dt + timedelta(minutes=dur)

    body = {
        "summary": ev.title,
        "location": ev.location or "",
        "description": ev.description or "",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": str(TZ)},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": str(TZ)},
    }
    return service.events().insert(calendarId="primary", body=body).execute()

# ================================================================
# Parsing en español
# ================================================================

def parse_datetime_es(text: str) -> Optional[datetime]:
    settings = {
        "TIMEZONE": str(TZ),
        "TO_TIMEZONE": str(TZ),
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    return dateparser.parse(text, languages=["es"], settings=settings)


def parse_event_from_text(text: str) -> PendingEvent:
    ev = PendingEvent()

    # Lugar: "en ..."
    m_loc = re.search(r"\ben\s+([^.,\n]+)", text, flags=re.IGNORECASE)
    if m_loc:
        ev.location = m_loc.group(1).strip()

    # Duración: "45 min" o "2 horas"
    m_min = re.search(r"(\d{1,3})\s*min", text, re.IGNORECASE)
    m_hr  = re.search(r"(\d{1,2})\s*hora", text, re.IGNORECASE)
    if m_min:
        ev.duration_min = int(m_min.group(1))
    elif m_hr:
        ev.duration_min = int(m_hr.group(1)) * 60

    # Fecha/Hora
    dt = parse_datetime_es(text)
    if dt:
        ev.start = dt

    # Título heurístico
    m_title = re.search(r"\b(con|para|sobre)\s+(.+)$", text, flags=re.IGNORECASE)
    if m_title:
        ev.title = m_title.group(2).strip()
    if not ev.title:
        # fallback: primeras 30 chars
        ev.title = text.strip()[:30]

    return ev

# ================================================================
# Emails (opcional)
# ================================================================

def send_email_notification(to_email: str, subject: str, body: str):
    if not (SENDER_EMAIL and SENDER_PASS):
        print("(Aviso) No hay SENDER_EMAIL/SENDER_PASS, omito correo.")
        return
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASS)
            msg = f"From: {SENDER_EMAIL}\nTo: {to_email}\nSubject: {subject}\n\n{body}"
            server.sendmail(SENDER_EMAIL, to_email, msg.encode("utf-8"))
        print("Correo enviado a", to_email)
    except Exception as e:
        print("Error enviando correo:", e)

# ================================================================
# Listar agenda (hoy / mañana / semana / fecha)
# ================================================================

def list_events(service, start_dt: datetime, end_dt: datetime):
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_dt.astimezone(TZ).isoformat(),
        timeMax=end_dt.astimezone(TZ).isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])
    if not events:
        print("No tienes eventos en ese rango ✨")
        return
    print("\nTus eventos:")
    for ev in events:
        start_str = ev['start'].get('dateTime') or ev['start'].get('date')
        end_str   = ev['end'].get('dateTime')   or ev['end'].get('date')
        title = ev.get('summary', '(Sin título)')
        try:
            if 'dateTime' in ev['start']:
                st = datetime.fromisoformat(start_str)
                et = datetime.fromisoformat(end_str)
                st_local = st.astimezone(TZ).strftime('%d/%m %H:%M')
                et_local = et.astimezone(TZ).strftime('%H:%M')
                print(f"- {st_local}-{et_local} · {title}")
            else:
                print(f"- Todo el día · {title}")
        except Exception:
            print(f"- {title}")

INTENT_SCHEDULE_WORDS = [
    'agenda', 'agendar', 'programa', 'programar', 'crea', 'crear', 'pon', 'poner', 'haz', 'hacer', 'calendariza', 'calendarizar'
]

INTENT_LIST_WORDS = [
    'qué tareas tengo', 'que tareas tengo', 'qué debo hacer', 'que debo hacer', 'mi agenda', 'ver agenda', 'qué hay', 'que hay', 'listar', 'lista'
]


def detect_intent(text: str) -> str:
    t = text.lower().strip()
    if any(w in t for w in INTENT_LIST_WORDS) or (t.startswith('agenda') and ('hoy' in t or 'mañana' in t)):
        if 'hoy' in t:
            return 'list_today'
        if 'mañana' in t:
            return 'list_tomorrow'
        if 'semana' in t or 'esta semana' in t:
            return 'list_week'
        if parse_datetime_es(t):
            return 'list_date'
        return 'list_today'
    if any(w in t for w in INTENT_SCHEDULE_WORDS):
        return 'create'
    if re.search(r"\b(\d{1,2}[:.]\d{2}|am|pm|\d{1,2}/\d{1,2})\b", t):
        return 'create'
    return 'unknown'

# ================================================================
# Interfaz de asistente en consola (Thonny)
# ================================================================

def solicitar(texto: str) -> str:
    try:
        return input(texto).strip()
    except EOFError:
        return ''


def pedir_fecha_hora() -> Optional[datetime]:
    while True:
        resp = solicitar("¿Para cuándo es la cita? (ej.: 'mañana 3pm', '12/09 14:30') → ")
        if not resp:
            return None
        dt = parse_datetime_es(resp)
        if dt:
            return dt
        print("No entendí la fecha/hora. Prueba otro formato.")


def pedir_titulo() -> Optional[str]:
    resp = solicitar("¿Cómo se llama la cita? (ej.: 'Dentista', 'Reunión con Ana') → ")
    return resp or None


def pedir_lugar() -> Optional[str]:
    resp = solicitar("¿Dónde es? (dirección, 'online', 'oficina', etc.) → ")
    return resp or None


def pedir_duracion() -> Optional[int]:
    resp = solicitar("¿Duración en minutos? (Enter para usar 60) → ")
    if not resp:
        return None
    try:
        v = int(resp)
        return v if v > 0 else None
    except:
        return None


def completar_datos(ev: PendingEvent) -> PendingEvent:
    if not ev.start:
        ev.start = pedir_fecha_hora()
    if not ev.title:
        ev.title = pedir_titulo()
    if not ev.location:
        ev.location = pedir_lugar()
    if not ev.duration_min:
        ev.duration_min = pedir_duracion() or DEFAULT_DURATION_MIN
    return ev


def confirmar_evento(ev: PendingEvent) -> bool:
    inicio = ev.start.astimezone(TZ).strftime('%d/%m %H:%M') if ev.start else '—'
    dur = ev.duration_min or DEFAULT_DURATION_MIN
    print("\nResumen del evento:")
    print(f"  Título   : {ev.title}")
    print(f"  Inicio   : {inicio} {TZ}")
    print(f"  Duración : {dur} min")
    print(f"  Lugar    : {ev.location or '—'}")
    print()
    r = solicitar("¿Confirmo y creo el evento en Google Calendar? (s/n) → ").lower()
    return r.startswith('s')


def flujo_notificacion(link: str, ev: PendingEvent):
    if not DEFAULT_NOTIFY_EMAIL:
        return
    inicio = ev.start.astimezone(TZ).strftime('%d/%m %H:%M') if ev.start else '—'
    subject = "Nueva cita agendada"
    body = (
        f"Se creó el evento '{ev.title}' el {inicio} en {ev.location or '—'}\n"
        f"Enlace en Calendar: {link}\n"
    )
    send_email_notification(DEFAULT_NOTIFY_EMAIL, subject, body)


def main():
    print("\n=== Asistente de Google Calendar ===")
    print("Puedes decir cosas como:")
    print("  - 'agenda una cita mañana a las 3pm con el dentista en zona 10 por 45 minutos'")
    print("  - 'qué debo hacer hoy?'  |  'qué tareas tengo para mañana?'  |  'ver mi agenda de esta semana'\n")
    print("Escribe 'salir' para terminar.")

    service = get_calendar_service()

    while True:
        texto = solicitar("\nDime: → ")
        if not texto or texto.lower() in {"salir", "exit", "quit"}:
            print("Hasta luego 👋")
            break

        intent = detect_intent(texto)

        if intent in {"list_today", "list_tomorrow", "list_week", "list_date"}:
            now = datetime.now(TZ)
            if intent == 'list_today':
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                list_events(service, start, end)
                continue
            if intent == 'list_tomorrow':
                start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                list_events(service, start, end)
                continue
            if intent == 'list_week':
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=7)
                list_events(service, start, end)
                continue
            if intent == 'list_date':
                dt = parse_datetime_es(texto)
                if dt:
                    start = dt.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
                    end = start + timedelta(days=1)
                    list_events(service, start, end)
                    continue

        if intent == 'create':
            ev = parse_event_from_text(texto)
            ev = completar_datos(ev)
            if not ev.start or not ev.title:
                print("Faltan datos esenciales (fecha/hora y título). Inténtalo de nuevo.")
                continue
            if not confirmar_evento(ev):
                print("Cancelado.")
                continue
            try:
                creado = create_event(service, ev)
                link = creado.get("htmlLink", "")
                print("\n✅ Evento creado en Google Calendar.")
                print("Enlace:", link)
                flujo_notificacion(link, ev)
            except Exception as e:
                print("❌ Error creando el evento:", e)
            continue

        # Desconocido: intenta como crear, si falla, ofrece ayuda general
        ev = parse_event_from_text(texto)
        if ev.start:
            ev = completar_datos(ev)
            if ev.start and ev.title and confirmar_evento(ev):
                try:
                    creado = create_event(service, ev)
                    link = creado.get("htmlLink", "")
                    print("\n✅ Evento creado en Google Calendar.")
                    print("Enlace:", link)
                    flujo_notificacion(link, ev)
                except Exception as e:
                    print("❌ Error creando el evento:", e)
                continue

        print("Puedo agendar y también listar tu agenda. Prueba: 'qué debo hacer hoy?' o 'qué tareas tengo para mañana?'")


if __name__ == "__main__":
    main()
