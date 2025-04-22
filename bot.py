#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# ----------------------------
# Настройки и авторизация
# ----------------------------
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# сколько операторов показывать на одной странице
OPERATORS_PER_PAGE = 30

# Состояния разговора
(
    CHOOSING_DATE,
    CHOOSING_SHIFT,
    CHOOSING_MODEL,
    CHOOSING_SURVEY,
    CONFIRM_IDENTITY,
    SELECT_OPERATOR,
    INPUT_START,
    INPUT_FINISH,
    INPUT_DIFF,
    CONFIRMATION,
) = range(10)

# Google Sheets API
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
)
gc = gspread.authorize(creds)
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
sh = gc.open_by_key(SPREADSHEET_ID)
main_ws = sh.worksheet("Main report")
models_ws = sh.worksheet("models_and_surveys")
ops_ws = sh.worksheet("operators")


# ----------------------------
# Утилиты для клавиатур
# ----------------------------
def build_date_keyboard() -> InlineKeyboardMarkup:
    tz = pytz.timezone(os.getenv("TIMEZONE"))
    today = datetime.now(tz).date()

    keyboard = []
    keyboard.append([
        InlineKeyboardButton(
            today.strftime("Сегодня (%d/%m/%Y)"),
            callback_data=today.isoformat()
        )
    ])
    for delta in range(1, 6):
        past = today - timedelta(days=delta)
        future = today + timedelta(days=delta)
        keyboard.append([
            InlineKeyboardButton(past.strftime("%d/%m/%Y"), callback_data=past.isoformat()),
            InlineKeyboardButton(future.strftime("%d/%m/%Y"), callback_data=future.isoformat()),
        ])
    return InlineKeyboardMarkup(keyboard)


def build_operators_keyboard(context: CallbackContext, page: int) -> InlineKeyboardMarkup:
    operators = context.user_data.get('operators', [])
    start = page * OPERATORS_PER_PAGE
    end = start + OPERATORS_PER_PAGE
    page_ops = operators[start:end]

    buttons = []
    for idx, op in enumerate(page_ops, start):
        buttons.append([InlineKeyboardButton(op, callback_data=f"OP_{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅ Предыдущие", callback_data="OP_PAGE_PREV"))
    if end < len(operators):
        nav_row.append(InlineKeyboardButton("Следующие ➡", callback_data="OP_PAGE_NEXT"))
    nav_row.append(InlineKeyboardButton("⬅ Назад", callback_data="BACK_SURVEY"))

    buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


# ----------------------------
# Хендлеры шагов разговора
# ----------------------------
def start(update: Update, context: CallbackContext) -> int:
    context.user_data['to_delete'] = [update.message.message_id]
    msg = update.message.reply_text(
        "Добрый день! Давайте начнём отчёт.\nВыберите дату смены:",
        reply_markup=build_date_keyboard(),
    )
    context.user_data['to_delete'].append(msg.message_id)
    return CHOOSING_DATE


def choose_date(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    iso_date = query.data
    context.user_data["date"] = datetime.fromisoformat(iso_date).strftime("%d/%m/%Y")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("NIGHT", callback_data="NIGHT"),
         InlineKeyboardButton("MORNING", callback_data="MORNING")],
        [InlineKeyboardButton("DAY", callback_data="DAY"),
         InlineKeyboardButton("EVENING", callback_data="EVENING")],
        [InlineKeyboardButton("⬅ Назад", callback_data="BACK_DATE")],
    ])
    query.edit_message_text("Выберите смену:", reply_markup=kb)
    return CHOOSING_SHIFT


def back_to_date(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        "Выберите дату смены:",
        reply_markup=build_date_keyboard(),
    )
    return CHOOSING_DATE


def choose_shift(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    context.user_data["shift"] = query.data

    models = sorted(set(models_ws.col_values(1)[1:]))
    buttons = [[InlineKeyboardButton(m, callback_data=m)] for m in models]
    buttons.append([InlineKeyboardButton("⬅ Назад", callback_data="BACK_SHIFT")])
    query.edit_message_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_MODEL


def back_to_shift(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("NIGHT", callback_data="NIGHT"),
         InlineKeyboardButton("MORNING", callback_data="MORNING")],
        [InlineKeyboardButton("DAY", callback_data="DAY"),
         InlineKeyboardButton("EVENING", callback_data="EVENING")],
        [InlineKeyboardButton("⬅ Назад", callback_data="BACK_DATE")],
    ])
    query.edit_message_text("Выберите смену:", reply_markup=kb)
    return CHOOSING_SHIFT


def choose_model(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    context.user_data["model"] = query.data

    rows = models_ws.get_all_values()[1:]
    surveys = [r[1] for r in rows if r[0] == context.user_data["model"]]
    buttons = [[InlineKeyboardButton(s, callback_data=s)] for s in surveys]
    buttons.append([InlineKeyboardButton("⬅ Назад", callback_data="BACK_MODEL")])
    query.edit_message_text("Выберите анкету:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_SURVEY


def back_to_model(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    models = sorted(set(models_ws.col_values(1)[1:]))
    buttons = [[InlineKeyboardButton(m, callback_data=m)] for m in models]
    buttons.append([InlineKeyboardButton("⬅ Назад", callback_data="BACK_SHIFT")])
    query.edit_message_text("Выберите модель:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_MODEL


def choose_survey(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    context.user_data["survey"] = query.data

    buttons = [
        [InlineKeyboardButton("Я", callback_data="ME"),
         InlineKeyboardButton("Другое", callback_data="OTHER")],
        [InlineKeyboardButton("⬅ Назад", callback_data="BACK_SURVEY")],
    ]
    query.edit_message_text("Кто вы?", reply_markup=InlineKeyboardMarkup(buttons))
    return CONFIRM_IDENTITY


def back_to_survey(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    rows = models_ws.get_all_values()[1:]
    surveys = [r[1] for r in rows if r[0] == context.user_data["model"]]
    buttons = [[InlineKeyboardButton(s, callback_data=s)] for s in surveys]
    buttons.append([InlineKeyboardButton("⬅ Назад", callback_data="BACK_MODEL")])
    query.edit_message_text("Выберите анкету:", reply_markup=InlineKeyboardMarkup(buttons))
    return CHOOSING_SURVEY


def confirm_identity(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    choice = query.data

    if choice == "ME":
        user = query.from_user
        username = user.username or f"{user.first_name} {user.last_name or ''}"
        context.user_data["operator"] = username
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="BACK_SURVEY")]])
        query.edit_message_text("Введите значение START:", reply_markup=kb)
        return INPUT_START
    else:
        operators = [op.strip() for op in ops_ws.col_values(1)[1:] if op.strip()]
        context.user_data['operators'] = operators
        context.user_data['operators_page'] = 0
        kb = build_operators_keyboard(context, page=0)
        query.edit_message_text("Выберите оператора:", reply_markup=kb)
        return SELECT_OPERATOR


def select_operator(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data

    if data == "OP_PAGE_NEXT":
        context.user_data['operators_page'] += 1
        kb = build_operators_keyboard(context, context.user_data['operators_page'])
        query.edit_message_text("Выберите оператора:", reply_markup=kb)
        return SELECT_OPERATOR

    if data == "OP_PAGE_PREV":
        context.user_data['operators_page'] -= 1
        kb = build_operators_keyboard(context, context.user_data['operators_page'])
        query.edit_message_text("Выберите оператора:", reply_markup=kb)
        return SELECT_OPERATOR

    if data.startswith("OP_"):
        idx = int(data.split("_", 1)[1])
        operator = context.user_data['operators'][idx]
        context.user_data["operator"] = operator
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="BACK_SURVEY")]])
        query.edit_message_text("Введите значение START:", reply_markup=kb)
        return INPUT_START

    return SELECT_OPERATOR


def input_start(update: Update, context: CallbackContext) -> int:
    context.user_data['to_delete'].append(update.message.message_id)
    text = update.message.text.strip()
    try:
        context.user_data["start"] = int(text)
    except ValueError:
        resp = update.message.reply_text("Пожалуйста, введите целое число (START).")
        context.user_data['to_delete'].append(resp.message_id)
        return INPUT_START

    resp = update.message.reply_text(
        "Введите значение FINISH:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="BACK_SURVEY")]])
    )
    context.user_data['to_delete'].append(resp.message_id)
    return INPUT_FINISH


def input_finish(update: Update, context: CallbackContext) -> int:
    context.user_data['to_delete'].append(update.message.message_id)
    text = update.message.text.strip()
    try:
        context.user_data["finish"] = int(text)
    except ValueError:
        resp = update.message.reply_text("Пожалуйста, введите целое число (FINISH).")
        context.user_data['to_delete'].append(resp.message_id)
        return INPUT_FINISH

    resp = update.message.reply_text(
        'Введите значение "+ OR -":',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Назад", callback_data="BACK_INPUT_FINISH")]])
    )
    context.user_data['to_delete'].append(resp.message_id)
    return INPUT_DIFF


def input_diff(update: Update, context: CallbackContext) -> int:
    context.user_data['to_delete'].append(update.message.message_id)
    text = update.message.text.strip()
    try:
        context.user_data["diff"] = int(text)
    except ValueError:
        resp = update.message.reply_text("Пожалуйста, введите целое число (с + или -).")
        context.user_data['to_delete'].append(resp.message_id)
        return INPUT_DIFF

    summary = (
        f"*Проверьте ваши данные:*\n"
        f"Дата смены: {context.user_data['date']}\n"
        f"Смена: {context.user_data['shift']}\n"
        f"Модель: {context.user_data['model']}\n"
        f"Анкета: {context.user_data['survey']}\n"
        f"Оператор: {context.user_data['operator']}\n"
        f"START: {context.user_data['start']}\n"
        f"FINISH: {context.user_data['finish']}\n"
        f"+ OR - : {context.user_data['diff']}"
    )
    resp = update.message.reply_text(
        summary,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Подтвердить", callback_data="CONFIRM")],
            [InlineKeyboardButton("Изменить",   callback_data="EDIT")],
        ]),
        parse_mode="Markdown"
    )
    context.user_data['to_delete'].append(resp.message_id)
    return CONFIRMATION


def save_report(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()

    tz = pytz.timezone(os.getenv("TIMEZONE"))
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    d = context.user_data
    row = [
        timestamp,
        d["date"],
        d["shift"],
        d["model"],
        d["survey"],
        d["operator"],
        d["start"],
        d["finish"],
        d["diff"],
    ]
    main_ws.append_row(row, value_input_option="USER_ENTERED")

    chat_id = query.message.chat_id
    for msg_id in context.user_data.get('to_delete', []):
        try:
            context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
    except Exception:
        pass

    final_text = (
        "✅ Записаны данные:\n"
        f"Дата смены: {d['date']}\n"
        f"Смена: {d['shift']}\n"
        f"Модель: {d['model']}\n"
        f"Анкета: {d['survey']}\n"
        f"Оператор: {d['operator']}\n"
        f"START: {d['start']}\n"
        f"FINISH: {d['finish']}\n"
        f"+ OR - : {d['diff']}"
    )
    context.bot.send_message(chat_id=chat_id, text=final_text)
    return ConversationHandler.END


def edit_report(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        "Хорошо, давайте начнём заново. Выберите дату смены:",
        reply_markup=build_date_keyboard()
    )
    return CHOOSING_DATE


def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Отчёт отменён.")
    return ConversationHandler.END


def restart(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    try:
        context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception:
        pass
    for msg_id in context.user_data.get('to_delete', []):
        try:
            context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    context.user_data.clear()
    return start(update, context)


def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    updater = Updater(
        TOKEN,
        use_context=True,
        request_kwargs={"connect_timeout": 10, "read_timeout": 20},
    )
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_DATE: [
                CallbackQueryHandler(choose_date, pattern=r"^\d{4}-\d{2}-\d{2}$"),
            ],
            CHOOSING_SHIFT: [
                CallbackQueryHandler(back_to_date, pattern="^BACK_DATE$"),
                CallbackQueryHandler(choose_shift, pattern="^(NIGHT|MORNING|DAY|EVENING)$"),
            ],
            CHOOSING_MODEL: [
                CallbackQueryHandler(back_to_shift, pattern="^BACK_SHIFT$"),
                CallbackQueryHandler(choose_model, pattern="^.+$"),
            ],
            CHOOSING_SURVEY: [
                CallbackQueryHandler(back_to_model, pattern="^BACK_MODEL$"),
                CallbackQueryHandler(choose_survey, pattern="^.+$"),
            ],
            CONFIRM_IDENTITY: [
                CallbackQueryHandler(back_to_survey, pattern="^BACK_SURVEY$"),
                CallbackQueryHandler(confirm_identity, pattern="^(ME|OTHER)$"),
            ],
            SELECT_OPERATOR: [
                CallbackQueryHandler(back_to_survey, pattern="^BACK_SURVEY$"),
                CallbackQueryHandler(select_operator, pattern="^(OP_PAGE_NEXT|OP_PAGE_PREV|OP_\\d+)$"),
            ],
            INPUT_START: [
                MessageHandler(Filters.text & ~Filters.command, input_start),
            ],
            INPUT_FINISH: [
                MessageHandler(Filters.text & ~Filters.command, input_finish),
            ],
            INPUT_DIFF: [
                MessageHandler(Filters.text & ~Filters.command, input_diff),
            ],
            CONFIRMATION: [
                CallbackQueryHandler(save_report, pattern="^CONFIRM$"),
                CallbackQueryHandler(edit_report, pattern="^EDIT$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("restart", restart),
        ],
    )

    dp.add_handler(conv)
    dp.add_handler(CommandHandler("restart", restart))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
