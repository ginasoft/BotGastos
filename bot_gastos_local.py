
import os
import logging
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import whisper
import pytesseract
from PIL import Image
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
model = whisper.load_model("base")

def connect_to_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
    client = gspread.authorize(creds)
    libro = client.open("RegistroGastos")
    return libro.sheet1, libro.worksheet("DetalleSuper")

sheet_main, sheet_detalle = connect_to_gsheet()

def clasificar(texto):
    texto = texto.lower()

    categorias = {
        ("super", "supermercado", "chino"): "Supermercado",
        ("farmacia",): "Salud",
        ("spotify", "netflix"): "Suscripción",
        ("uber", "cabify", "remis"): "Transporte",
        ("comida", "cafetería", "cafe", "panadería", "almuerzo", "cena"): "Alimentación",
        ("luz", "gas", "agua", "internet", "servicio", "servicios"): "Servicios",
        ("tarjeta",): "Tarjeta",
        ("alquiler",): "Vivienda",
        ("sushi", "rappi", "rapi", "wrapi", "pedido ya", "pedidos ya", "delivery"): "Delivery",
        ("viaje", "uruguay", "extranjero", "turismo", "usd", "dólares", "dolares"): "Viaje"
    }

    medios_pago = {
        "débito": "Tarjeta débito",
        "debito": "Tarjeta débito",
        "crédito": "Tarjeta crédito",
        "credito": "Tarjeta crédito",
        "master": "MasterCard",
        "visa": "Visa",
        "efectivo": "Efectivo"
    }

    monedas = {
        "usd": "USD", "dólares": "USD", "dolares": "USD",
        "pesos uruguayos": "UYU", "uy": "UYU", "uyu": "UYU",
        "ars": "ARS", "pesos": "ARS", "peso argentino": "ARS",
        "clp": "CLP", "chilenos": "CLP", "peso chileno": "CLP",
        "bob": "BOB", "bolivianos": "BOB", "boliviano": "BOB",
        "brl": "BRL", "reales": "BRL", "real": "BRL",
        "eur": "EUR", "euros": "EUR", "euro": "EUR",
        "gbp": "GBP", "libras": "GBP", "libra": "GBP"
    }

    categoria = "Otro"
    medio = "No especificado"
    recurrente = "Sí" if "suscripción" in texto or "todos los meses" in texto else "No"
    comentario = texto
    moneda = "ARS"

    for claves, valor in categorias.items():
        if any(k in texto for k in claves):
            categoria = valor
            break

    for clave, valor in monedas.items():
        if clave in texto:
            moneda = valor
            break

    for clave, valor in medios_pago.items():
        if clave in texto:
            medio = valor
            break

    monto = extraer_total(texto)
    return categoria, medio, recurrente, comentario, moneda, monto

def extraer_total(texto):
    total_line = re.findall(r"total[^\d]*(\d+[.,]\d{1,2})", texto.lower())
    if total_line:
        try:
            return float(total_line[-1].replace(",", "."))
        except:
            pass
    return extraer_monto(texto)

def extraer_monto(texto):
    numeros = re.findall(r"\b\d{1,8}(?:[.,]\d{1,2})?\b", texto.replace(",", "."))
    if numeros:
        try:
            return float(numeros[0])
        except:
            return None
    return None

def extraer_items_por_linea(texto, fecha, usuario):
    items = []
    lineas = texto.split("\n")
    for linea in lineas:
        match = re.match(r"([\w\s]+?)\s*(\d+(?:[.,]\d{1,2})?)$", linea.strip())
        if match:
            producto = match.group(1).strip().capitalize()
            precio = match.group(2).replace(",", ".")
            try:
                precio = float(precio)
                items.append([fecha, usuario, producto, "", "", precio])
            except:
                continue
    return items

pendientes = {}

async def solicitar_confirmacion(update: Update, texto: str, tipo: str):
    categoria, medio, recurrente, comentario, moneda, monto = clasificar(texto)
    fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_id = update.effective_user.id
    usuario = update.message.from_user.first_name

    if monto is None:
        await update.message.reply_text("⚠️ No se detectó un monto válido. Por favor indicá el valor numérico del gasto.")
        return

    detalle_items = extraer_items_por_linea(texto, fecha, usuario)
    pendientes[user_id] = {
        "resumen": [fecha, usuario, tipo, f"${monto}", categoria, moneda, medio, recurrente, comentario],
        "items": detalle_items
    }

    mensaje = (
        f"¿Confirmás el siguiente gasto?\n\n"
        f"🗓 Fecha: {fecha}\n"
        f"👤 Usuario: {usuario}\n"
        f"📝 Tipo: {tipo}\n"
        f"💸 Monto: ${monto}\n"
        f"📂 Categoría: {categoria}\n"
        f"💱 Moneda: {moneda}\n"
        f"💳 Medio de pago: {medio}\n"
        f"🔁 Recurrente: {recurrente}"
    )

    botones = [[
        InlineKeyboardButton("✅ Confirmar", callback_data="confirmar"),
        InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")
    ]]

    await update.message.reply_text(mensaje, reply_markup=InlineKeyboardMarkup(botones))

async def manejar_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "confirmar" and user_id in pendientes:
        resumen = pendientes[user_id]["resumen"]
        items = pendientes[user_id]["items"]
        sheet_main.append_row(resumen)
        if items:
            sheet_detalle.append_rows(items)
        await query.edit_message_text("✅ Gasto registrado correctamente.")
        del pendientes[user_id]
    elif query.data == "cancelar":
        await query.edit_message_text("❌ Registro cancelado.")
        if user_id in pendientes:
            del pendientes[user_id]

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.voice.get_file()
    await file.download_to_drive("audio.ogg")
    result = model.transcribe("audio.ogg")
    await solicitar_confirmacion(update, result["text"], "Audio")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.photo[-1].get_file()
    await file.download_to_drive("ticket.jpg")
    texto = pytesseract.image_to_string(Image.open("ticket.jpg"))
    await solicitar_confirmacion(update, texto, "Foto")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await solicitar_confirmacion(update, update.message.text, "Texto")

app = ApplicationBuilder().token("BOT_TOKEN").build()
app.add_handler(MessageHandler(filters.VOICE, handle_audio))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(CallbackQueryHandler(manejar_confirmacion))
app.run_polling()
