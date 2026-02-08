from urllib.parse import quote
from datetime import date, datetime, time

# --------------------
# Telefones
# --------------------
def to_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def phone_to_e164_br(phone: str) -> str:
    """
    Recebe: '31999613652' / '(31) 99961-3652' / '5531999613652'
    Retorna: '5531999613652'
    """
    d = to_digits(phone)
    if d.startswith("55"):
        return d
    return "55" + d

def phone_pretty_br(phone: str) -> str:
    """
    Retorna: (31) 99961-3652
    """
    d = to_digits(phone)
    if d.startswith("55"):
        d = d[2:]

    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return phone

def whatsapp_link(phone: str, message: str) -> str:
    return f"https://wa.me/{phone_to_e164_br(phone)}?text={quote(message)}"


# --------------------
# Datas / horas
# --------------------
def fmt_date_br(d) -> str:
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    return str(d)

def fmt_time_br(t) -> str:
    if isinstance(t, time):
        return t.strftime("%H:%M")
    if isinstance(t, datetime):
        return t.strftime("%H:%M")
    return str(t)


# --------------------
# Mensagens
# --------------------
def msg_para_borogodo(r) -> str:
    obs = f"\n📝 Obs: {r.obs}" if getattr(r, "obs", "") else ""

    return (
        "🍽️ *Nova reserva — Borogodó*\n\n"
        f"👤 Nome: {r.nome}\n"
        f"📅 Data: {fmt_date_br(r.data)}\n"
        f"⏰ Hora: {fmt_time_br(r.hora)}\n"
        f"👥 Pessoas: {r.pessoas}\n"
        f"📞 Telefone: {phone_pretty_br(r.telefone)}\n"
        f"{obs}\n\n"
        "✅ Confirmar no sistema e responder o cliente."
    )

def msg_para_cliente(r) -> str:
    return (
        "🍽️ *Borogodó — Reserva recebida ✅*\n\n"
        f"Olá, {r.nome}! 💛\n\n"
        "Recebemos seu pedido de reserva:\n"
        f"📅 {fmt_date_br(r.data)}\n"
        f"⏰ {fmt_time_br(r.hora)}\n"
        f"👥 {r.pessoas} pessoa(s)\n\n"
        "Assim que confirmarmos a mesa, te avisamos por aqui. 😊"
    )
