#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import json
import threading
import os
from datetime import datetime, timedelta
from pathlib import Path
from config import *

# Таблица переходов для кнопки "Назад"
BACK_TRANSITIONS = {
    "waiting_txid": "waiting_screenshot",
    "waiting_screenshot": "payment_intro",
    "payment_intro": "menu"
}

# Тарифы и цены
PLANS = {
    "1m": {"name": "1 месяц", "price": 39, "days": 30},
    "3m": {"name": "3 месяца", "price": 99, "days": 90},
    "lifetime": {"name": "Пожизненно", "price": 239, "days": None}
}

# Сообщения статусов
STATUS_MESSAGES = {
    "active": "✅ Активна",
    "pending": "⏳ Ожидает подтверждения",
    "expired": "❌ Истекла",
    "none": "❌ Не активна"
}

from db import Database

class SignalBot:
    def __init__(self):
        """Инициализация бота"""
        self.token = TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.db = Database()
        self.last_message_id = None
        self.running = False
        self.last_backup_date = None
        
        # Запускаем логирование старта
        self.send_log("[BOT] Запущен...")
    
    def safe_parse_date(self, value):
        """Безопасно конвертирует ISO-дату в datetime или None"""
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        return None
    
    def send_request(self, method, params=None):
        """Отправка запроса к Telegram API с обработкой ошибок"""
        try:
            url = f"{self.base_url}/{method}"
            response = requests.post(url, json=params, timeout=30)
            
            # Обработка HTTP ошибок (игнорируем timeout и 409)
            if response.status_code == 400:
                # Игнорируем устаревшие callback-запросы Telegram
                if "query is too old" in response.text or "query ID is invalid" in response.text:
                    return None
                error_msg = f"[ERROR] Bad Request 400: {response.text}"
                print(error_msg)
                self.send_log(error_msg)
                return None
            elif response.status_code == 409:
                # Игнорируем 409 конфликты
                return None
            elif response.status_code == 429:
                # Rate limit - небольшая задержка
                time.sleep(1)
                return None
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            # Игнорируем timeout ошибки
            return None
        except requests.exceptions.RequestException as e:
            # Игнорируем некоторые ошибки, логируем только важные
            if "no such column" not in str(e).lower():
                error_msg = f"[ERROR] API запрос {method}: {e}"
                print(error_msg)
                self.send_log(error_msg)
            return None
        except Exception as e:
            # Игнорируем некоторые ошибки, логируем только важные
            if "timeout" not in str(e).lower() and "no such column" not in str(e).lower():
                error_msg = f"[ERROR] Неожиданная ошибка в {method}: {e}"
                print(error_msg)
                self.send_log(error_msg)
            return None
    
    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        """Отправка безопасного сообщения пользователю"""
        try:
            if not chat_id or not isinstance(chat_id, (int, str)):
                self.send_log(f"[WARN] Пропущена отправка — неверный chat_id: {chat_id}")
                return False

            params = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode
            }
            if reply_markup:
                params["reply_markup"] = reply_markup

            response = self.send_request("sendMessage", params)
            if not response or not response.get("ok"):
                desc = response.get("description", "unknown error") if response else "no response"
                self.send_log(f"[WARN] Не удалось отправить сообщение ({chat_id}): {desc}")
                return False

            return True

        except Exception as e:
            if "chat not found" not in str(e).lower():
                error_msg = f"[ERROR] Отправка сообщения в {chat_id}: {e}"
                print(error_msg)
                self.send_log(error_msg)
            return False
    
    def send_media_group(self, chat_id, media):
        """Отправка альбома (нескольких фото)"""
        try:
            params = {
                "chat_id": chat_id,
                "media": json.dumps(media)
            }
            
            result = self.send_request("sendMediaGroup", params)
            return result is not None and result.get("ok", False)
        except Exception as e:
            error_msg = f"[ERROR] Отправка альбома в {chat_id}: {e}"
            print(error_msg)
            self.send_log(error_msg)
            return False
    
    def send_photo(self, chat_id, photo_path, caption=None, parse_mode="HTML"):
        """Безопасная отправка фото"""
        try:
            if not chat_id or not isinstance(chat_id, (int, str)):
                self.send_log(f"[WARN] Пропущена отправка фото — неверный chat_id: {chat_id}")
                return False

            if not os.path.exists(photo_path):
                self.send_log(f"[ERROR] Файл не найден: {photo_path}")
                return False

            with open(photo_path, "rb") as photo_file:
                response = requests.post(
                    f"{self.base_url}/sendPhoto",
                    data={"chat_id": chat_id, "caption": caption or "", "parse_mode": parse_mode},
                    files={"photo": photo_file},
                    timeout=20
                )

            if response.status_code == 400 and "chat not found" in response.text:
                self.send_log(f"[WARN] Не удалось отправить фото — chat not found (chat_id={chat_id})")
                return False

            response.raise_for_status()
            return True

        except Exception as e:
            if "chat not found" not in str(e).lower():
                error_msg = f"[ERROR] Отправка фото в {chat_id}: {e}"
                print(error_msg)
                self.send_log(error_msg)
            return False
    
    def send_signal_intro(self, chat_id, intro_text):
        """Отправка введения с примером сигнала"""
        try:
            # Попытка отправить фото с примером сигнала
            signal_example_url = SIGNAL_EXAMPLE_URL
            
            success = self.send_photo(chat_id, signal_example_url, intro_text, "HTML")
            
            if not success:
                # Если фото не отправилось, отправляем альбом с примерами
                self.send_signal_examples(chat_id)
            
            # Добавляем кнопки после введения
            keyboard = self.create_reply_keyboard([
                ["💰 Оплата"],
                ["🧾 Поддержка", "🆘 Помощь"]
            ])
            self.send_message(chat_id, "💡 Выберите действие для продолжения:", keyboard)
                
        except Exception as e:
            error_msg = f"[ERROR] Отправка введения сигнала: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def send_signal_examples(self, chat_id):
        """Отправка альбома с примерами сигналов"""
        try:
            # Примеры медиа-файлов (в реальном проекте замените на реальные URL)
            media = [
                {
                    "type": "photo", 
                    "media": "https://via.placeholder.com/800x600/1f2937/ffffff?text=📊+Реальные+сигналы+от+трейдеров+SignalBot+Pro",
                    "caption": "📊 Реальные сигналы от трейдеров SignalBot Pro"
                },
                {
                    "type": "photo", 
                    "media": "https://via.placeholder.com/800x600/059669/ffffff?text=💰+Прибыль+за+сентябрь:+%2B18.5%25",
                    "caption": "💰 Прибыль за сентябрь: +18.5%"
                },
                {
                    "type": "photo", 
                    "media": "https://via.placeholder.com/800x600/7c3aed/ffffff?text=📈+Вход,+тейк-профит+и+стоп+—+всё+чётко+и+просто",
                    "caption": "📈 Вход, тейк-профит и стоп — всё чётко и просто"
                }
            ]
            
            # Отправляем альбом
            success = self.send_media_group(chat_id, media)
            
            if success:
                # После альбома отправляем призыв к действию
                follow_up_text = """🔥 Это лишь часть наших сигналов за прошлый месяц.
👉 Хочешь получать их первым? Выбери тариф в разделе 💰 Оплата."""
                self.send_message(chat_id, follow_up_text)
            else:
                # Если альбом не отправился, отправляем текстовое сообщение
                fallback_text = """📊 Примеры наших торговых сигналов:

🔥 BTC/USDT LONG
📈 Вход: $45,200
🎯 Тейк-профит: $47,500 (+5.1%)
🛡 Стоп-лосс: $43,800 (-3.1%)
⏰ Время: 14:30 UTC

🔥 ETH/USDT SHORT  
📉 Вход: $3,150
🎯 Тейк-профит: $3,050 (+3.2%)
🛡 Стоп-лосс: $3,220 (-2.2%)
⏰ Время: 16:45 UTC

💎 Прибыль за сентябрь: +18.5%

👉 Хочешь получать такие сигналы первым? Выбери тариф в разделе 💰 Оплата."""
                self.send_message(chat_id, fallback_text)
                
        except Exception as e:
            error_msg = f"[ERROR] Отправка примеров сигналов: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def forward_message(self, from_chat_id, to_chat_id, message_id):
        """Пересылка сообщения"""
        try:
            params = {
                "chat_id": to_chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id
            }
            
            result = self.send_request("forwardMessage", params)
            return result is not None and result.get("ok", False)
        except Exception as e:
            error_msg = f"[ERROR] Пересылка сообщения {message_id} в {to_chat_id}: {e}"
            print(error_msg)
            return False
    
    def get_updates(self, offset=None, timeout=30):
        """Получение обновлений от Telegram"""
        try:
            params = {"timeout": timeout}
            if offset:
                params["offset"] = offset
            
            result = self.send_request("getUpdates", params)
            if result and result.get("ok"):
                return result.get("result", [])
            return []
        except Exception as e:
            error_msg = f"[ERROR] Получение обновлений: {e}"
            print(error_msg)
            self.send_log(error_msg)
            return []
    
    def send_log(self, text):
        """Отправка лога в канал"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_message = f"[{timestamp}] {text}"
            
            # Пытаемся отправить в канал
            success = self.send_message(LOG_CHANNEL_ID, log_message)
            
            if not success:
                print(f"Не удалось отправить лог в канал: {log_message}")
            
        except Exception as e:
            error_msg = f"[ERROR] Логирование: {e}"
            print(error_msg)
    
    def send_file_log(self, file_id, username, user_id):
        """Отправка фото напрямую в лог-канал"""
        try:
            self.send_request("sendPhoto", {
                "chat_id": LOG_CHANNEL_ID,
                "photo": file_id,
                "caption": f"[SCREENSHOT]\nUser: @{username or 'unknown'} (ID {user_id})"
            })
        except Exception as e:
            print(f"[ERROR] send_file_log: {e}")
    
    def create_reply_keyboard(self, buttons):
        """Создание Reply Keyboard (кнопки под строкой ввода)"""
        try:
            keyboard = []
            for row in buttons:
                keyboard_row = []
                for button in row:
                    keyboard_row.append(button)
                keyboard.append(keyboard_row)
            
            return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}
        except Exception as e:
            error_msg = f"[ERROR] Создание клавиатуры: {e}"
            print(error_msg)
            self.send_log(error_msg)
            return None
    
    def create_inline_keyboard(self, buttons):
        """Создание inline клавиатуры"""
        try:
            keyboard = []
            for row in buttons:
                keyboard_row = []
                for button in row:
                    keyboard_row.append({
                        "text": button["text"],
                        "callback_data": button["callback_data"]
                    })
                keyboard.append(keyboard_row)
            
            return {"inline_keyboard": keyboard}
        except Exception as e:
            error_msg = f"[ERROR] Создание inline клавиатуры: {e}"
            print(error_msg)
            self.send_log(error_msg)
            return None
    
    def handle_start(self, chat_id, user_id, username):
        """Обработка команды /start"""
        try:
            # Регистрируем пользователя, если новый
            if not self.db.user_exists(user_id):
                self.db.add_user(user_id, username)
                self.send_log(f"[NEW USER] @{username} (ID: {user_id})")

            # Главное меню с постоянной кнопкой "Помощь"
            keyboard = self.create_reply_keyboard([
                ["📈 Получать сигналы"],
                ["💰 Оплата"],
                ["ℹ️ Мой статус"],
                ["🧾 Поддержка", "🆘 Помощь"]
            ])

            welcome_text = (
                "Добро пожаловать в PTT Trades!\n\n"
                "Этот бот создан, чтобы давать тебе доступ к торговым сигналам и аналитике.\n"
                "Здесь ты можешь получить сигналы, оплатить подписку, узнать свой статус и связаться с поддержкой."
            )

            self.send_message(chat_id, welcome_text, keyboard)

        except Exception as e:
            error_msg = f"[ERROR] Обработка /start: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_get_signals(self, chat_id, user_id):
        """Отправка описания сигналов и примеров"""
        try:
            import json

            data_dir = os.path.join(os.path.dirname(__file__), "data")
            photo1_path = os.path.join(data_dir, "photo.jpg")
            photo2_path = os.path.join(data_dir, "fuck.jpg")

            caption_text = (
                "PTT Trades — твой персональный помощник в мире Forex.\n\n"
                "Ты получаешь не просто сигналы, а полные разборы сделок с аналитикой и сопровождением.\n\n"
                "Каждый сигнал включает:\n"
                "• Точку входа и выхода\n"
                "• Логику входа и подтверждение структуры\n"
                "• Сопровождение сделки\n"
                "• Рекомендации по управлению рисками\n\n"
                "Подходит для:\n"
                "— Владельцев проп-счетов (риск 1%)\n"
                "— Трейдеров с личным капиталом\n"
                "— Всех, кто хочет понимать рынок, а не угадывать направление\n\n"
                "Формат сигналов — как на примере ниже: четко, лаконично, профессионально.\n\n"
                "Присоединяйся к нашему сообществу и начни торговать осознанно."
            )

            keyboard = self.create_reply_keyboard([["↩️ Назад"]])

            if not os.path.exists(photo1_path) or not os.path.exists(photo2_path):
                missing = []
                if not os.path.exists(photo1_path):
                    missing.append("photo.jpg")
                if not os.path.exists(photo2_path):
                    missing.append("fuck.jpg")
                self.send_message(chat_id, f"Не найдены изображения: {', '.join(missing)}", keyboard)
                return

            # Формируем альбом из двух фото
            files = {
                "photo1": open(photo1_path, "rb"),
                "photo2": open(photo2_path, "rb")
            }

            media = [
                {"type": "photo", "media": "attach://photo1"},
                {"type": "photo", "media": "attach://photo2"}
            ]

            # Отправляем альбом
            requests.post(
                f"{self.base_url}/sendMediaGroup",
                data={"chat_id": chat_id, "media": json.dumps(media)},
                files=files,
                timeout=30
            )

            # Отправляем описание
            self.send_message(chat_id, caption_text, keyboard)

        except Exception as e:
            error_msg = f"[ERROR] handle_get_signals: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    
    def handle_help_faq(self, chat_id):
        """Раздел 'Помощь' — часто задаваемые вопросы"""
        try:
            help_text = """
🧠 <b>Добро пожаловать в раздел помощи!</b>

Здесь ты найдёшь ответы на самые частые вопросы,  
которые помогут тебе уверенно работать с сигналами и управлять рисками 💼

━━━━━━━━━━━━━━━━━━━
❓ <b>Какой риск ставить на позицию?</b>  
Если ты торгуешь на <b>проп-счёте</b> — риск строго <b>1%</b>.  
Если это <b>личный депозит</b>, можно ставить риск от <b>1%</b> до комфортного значения по своей <b>RM-системе</b> ⚖️.
━━━━━━━━━━━━━━━━━━━

📈 <b>Будет ли сопровождение по сделке?</b>  
Да ✅ — каждая сделка сопровождается до <b>тейка</b>, <b>стопа</b> или перевода в <b>безубыток (БУ)</b>.  
Все обновления публикуются <b>автоматически</b> в этом боте 💬 — ты всегда в курсе происходящего.
━━━━━━━━━━━━━━━━━━━

📞 <b>Если остались вопросы</b> — напиши администратору:
👉 <a href="https://t.me/PTTmanager">@PTTmanager</a>
"""
            keyboard = self.create_reply_keyboard([
                ["🧾 Поддержка"],
                ["↩️ Назад"]
            ])
            self.send_message(chat_id, help_text, reply_markup=keyboard, parse_mode="HTML")

        except Exception as e:
            error_msg = f"[ERROR] Обработка помощи: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_payment_start(self, chat_id, user_id):
        """Начало процесса оплаты - выбор тарифа"""
        try:
            payment_text = "Выберите подходящий тариф:"
            
            keyboard = self.create_reply_keyboard([
                ["1 месяц — 39 USDT"],
                ["3 месяца — 99 USDT"],
                ["Пожизненно — 239 USDT"],
                ["↩️ Назад"]
            ])
            self.send_message(chat_id, payment_text, keyboard)
            
            # Устанавливаем состояние для кнопки "Назад"
            self.db.set_user_state(user_id, "payment_intro")
            
        except Exception as e:
            error_msg = f"[ERROR] Начало оплаты: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_plan_selection(self, chat_id, user_id, plan_key):
        """Обработка выбора плана"""
        try:
            plan = PLANS.get(plan_key)
            if not plan:
                self.send_message(chat_id, "❌ Неверный тариф. Попробуйте еще раз.")
                return
            
            # Показываем методы оплаты для выбранного тарифа
            payment_text = "Выберите способ оплаты:"
            
            # Создаем Reply Keyboard кнопки для выбора метода оплаты
            keyboard = self.create_reply_keyboard([
                ["💰 Оплатить криптой (TRC20)"],
                ["⚡ Оплатить через Tribute"],
                ["↩️ Назад"]
            ])
            
            self.send_message(chat_id, payment_text, keyboard)
            
            # Сохраняем выбранный план в состоянии пользователя
            self.db.set_user_state(user_id, f"payment_method_{plan_key}")
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка выбора плана: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_crypto_payment(self, chat_id, user_id, plan_key):
        """Обработка выбора криптооплаты"""
        try:
            plan = PLANS.get(plan_key)
            if not plan:
                self.send_message(chat_id, "❌ Неверный тариф. Попробуйте еще раз.")
                return
            
            # Показываем адрес для оплаты
            payment_text = f"""Отправьте оплату на адрес:
TRC20: {CRYPTO_ADDRESS}
После перевода прикрепите скрин перевода."""
            
            keyboard = self.create_reply_keyboard([
                ["📸 Отправить скрин"],
                ["↩️ Назад"]
            ])
            self.send_message(chat_id, payment_text, keyboard)
            
            # Устанавливаем состояние ожидания скриншота с информацией о методе оплаты
            self.db.set_user_state(user_id, f"waiting_screenshot_crypto_{plan_key}")
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка криптооплаты: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_tribute_payment(self, chat_id, user_id, plan_key):
        """Обработка выбора оплаты через Tribute"""
        try:
            plan = PLANS.get(plan_key)
            if not plan:
                self.send_message(chat_id, "❌ Неверный тариф. Попробуйте еще раз.")
                return
            
            # Показываем кнопку для открытия Tribute
            payment_text = """Оплата через Tribute осуществляется в официальном Telegram mini app.
Нажмите кнопку ниже, чтобы перейти 👇"""
            
            # Создаем inline кнопку для открытия Tribute
            keyboard = {
                "inline_keyboard": [[
                    {"text": "💸 Открыть Tribute", "url": TRIBUTE_LINK}
                ]],
                "resize_keyboard": True
            }
            
            self.send_message(chat_id, payment_text, keyboard)
            
            # После оплаты через Tribute пользователь вернется и отправит скрин
            keyboard_back = self.create_reply_keyboard([["↩️ Назад"]])
            self.send_message(chat_id, "После завершения оплаты в Tribute отправьте скриншот:", keyboard_back)
            
            # Устанавливаем состояние ожидания скриншота с информацией о методе оплаты
            self.db.set_user_state(user_id, f"waiting_screenshot_tribute_{plan_key}")
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка оплаты через Tribute: {e}"
            print(error_msg)
            self.send_log(error_msg)

    def handle_payment_done(self, chat_id, user_id):
        """Обработка кнопки 'Я оплатил' - Шаг 2"""
        try:
            self.send_message(chat_id, "📸 Отправьте скриншот вашей транзакции.")
            
            # Создаем клавиатуру с кнопкой "Назад"
            keyboard = self.create_reply_keyboard([["↩️ Назад"]])
            self.send_message(chat_id, "Нажмите кнопку 'Назад' чтобы вернуться в меню", keyboard)
            
            # Устанавливаем состояние ожидания скриншота
            self.db.set_user_state(user_id, "waiting_screenshot")
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка 'Я оплатил': {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_screenshot(self, chat_id, user_id, username, file_id, user_state=None):
        """Обработка получения скриншота"""
        try:
            # Определяем метод оплаты и план из состояния пользователя
            payment_method = "crypto"
            plan_key = None
            
            if user_state:
                if user_state.startswith("waiting_screenshot_crypto_"):
                    payment_method = "crypto"
                    plan_key = user_state.replace("waiting_screenshot_crypto_", "")
                elif user_state.startswith("waiting_screenshot_tribute_"):
                    payment_method = "tribute"
                    plan_key = user_state.replace("waiting_screenshot_tribute_", "")
            
            # Создаем новый платеж с информацией о методе и плане
            payment_id = self.db.add_payment(
                user_id, 
                screenshot_file_id=file_id, 
                status="pending", 
                payment_method=payment_method,
                plan=plan_key
            )
            
            # Обновляем план пользователя
            if plan_key:
                self.db.update_user_status(user_id, "pending", plan_key)
            
            # Логируем скриншот в локальный файл
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            try:
                with open("screenshots_log.txt", "a", encoding="utf-8") as f:
                    f.write(f"{current_time} | @{username or 'unknown'} | ID {user_id} | method: {payment_method} | plan: {plan_key} | file_id: {file_id}\n")
            except Exception as log_error:
                print(f"[LOG ERROR] Не удалось записать скриншот в файл: {log_error}")
            
            # Отправляем кликабельную ссылку в лог-канал
            self.send_file_log(file_id, username, user_id)
            
            # Отправляем подтверждение
            self.send_message(chat_id, "✅ Скрин получен, ожидайте подтверждения администратора.")
            
            # Логируем платеж в канал в новом формате
            plan_name = PLANS.get(plan_key, {}).get("name", "Unknown") if plan_key else "Unknown"
            
            log_message = f"""[NEW PAYMENT]
user: @{username or 'unknown'} (ID {user_id})
method: {payment_method}
tariff: {plan_name}
status: pending"""
            
            self.send_log(log_message)
            
            # Сбрасываем состояние пользователя
            self.db.set_user_state(user_id, None)
            
            # Показываем сообщение о завершении без FAQ
            keyboard = self.create_reply_keyboard([
                ["📈 Получать сигналы", "💰 Оплата"],
                ["ℹ️ Мой статус"],
                ["🧾 Поддержка", "🆘 Помощь"]
            ])
            self.send_message(chat_id, "✅ Спасибо! Ваш платёж отправлен на проверку.", keyboard)
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка скриншота: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_txid(self, chat_id, user_id, username, txid):
        """Обработка получения TXID - устаревший метод, сохранен для совместимости"""
        try:
            # Этот метод больше не используется в новой логике оплаты
            # TXID не требуется, только скриншот
            self.send_message(chat_id, "❌ TXID больше не требуется. Отправьте только скриншот транзакции.")
            
        except Exception as e:
            error_msg = f"[ERROR] Обработка TXID: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_status(self, chat_id, user_id):
        """Обработка запроса статуса"""
        try:
            user = self.db.get_user(user_id)
            if user:
                # Правильно парсим даты из базы
                start_date = None
                end_date = None
                
                if user.get("start_date"):
                    try:
                        start_date = datetime.fromisoformat(user["start_date"])
                    except (ValueError, TypeError):
                        start_date = None
                
                if user.get("end_date"):
                    try:
                        end_date = datetime.fromisoformat(user["end_date"])
                    except (ValueError, TypeError):
                        end_date = None
                
                plan_key = user.get("plan")
                plan_name = PLANS.get(plan_key, {}).get("name", "Не выбран") if plan_key else "Не выбран"
                
                # Получаем информацию о последнем платеже
                payment = self.db.get_user_payment(user_id)
                payment_method = "Не указан"
                if payment:
                    payment_method = "Crypto" if payment.get("payment_method") == "crypto" else "Tribute"
                
                # Формируем базовую информацию
                start_date_str = start_date.strftime("%d.%m.%Y") if start_date else "Не указана"
                end_date_str = "Бессрочно" if end_date is None else end_date.strftime("%d.%m.%Y")
                
                # Определяем статус с учетом текущего времени
                current_time = datetime.now()
                
                if user["status"] == "active":
                    if user["plan"] == "lifetime" or end_date is None:
                        status_text = "✅ Активна"
                        end_date_str = "Бессрочно"
                    else:
                        if end_date and end_date > current_time:
                            status_text = "✅ Активна"
                        else:
                            status_text = "⚠️ Истекла"
                            # Обновляем статус в базе если подписка истекла
                            if end_date and end_date <= current_time:
                                self.db.update_user_status(user_id, "expired")
                elif user["status"] == "pending":
                    status_text = "⏳ Ожидает подтверждения"
                elif user["status"] == "expired":
                    status_text = "⚠️ Истекла"
                else:
                    status_text = "❌ Неактивна"
                
                message = f"""📊 Статус: {status_text}
📦 Тариф: {plan_name}
💰 Метод оплаты: {payment_method}
📅 Начало подписки: {start_date_str}
📅 Окончание: {end_date_str}"""
                
                # Добавляем специальные сообщения для истекших/неактивных подписок
                if user["status"] in ["expired", "none"]:
                    if user["status"] == "expired":
                        message += "\n\n⚠️ Ваша подписка истекла. Чтобы продлить доступ, нажмите «💰 Оплата»."
                    else:
                        message += "\n\n❌ У вас нет активной подписки. Нажмите «💰 Оплата» чтобы оформить доступ."
                    
            else:
                message = "❌ Пользователь не найден. Попробуйте команду /start"
            
            self.send_message(chat_id, message)
            
            # Добавляем кнопки в зависимости от статуса
            if user and user["status"] in ["expired", "none"]:
                keyboard = self.create_reply_keyboard([
                    ["💰 Оплата"],
                    ["↩️ Назад"]
                ])
            else:
                keyboard = self.create_reply_keyboard([["↩️ Назад"]])
            
            self.send_message(chat_id, "Выберите действие:", keyboard)
        except Exception as e:
            error_msg = f"[ERROR] Обработка статуса: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_support(self, chat_id):
        """Раздел 'Поддержка' — контакт администратора"""
        try:
            support_text = """
🧾 <b>Поддержка пользователей</b>

Если у тебя возникли вопросы, проблемы с оплатой  
или ты хочешь уточнить детали — напиши напрямую 👇

👤 <b>Администратор:</b> <a href="https://t.me/PTTmanager">@PTTmanager</a>

⏰ Ответ обычно в течение 1–2 часов.
"""
            keyboard = self.create_reply_keyboard([
                ["🆘 Помощь"],
                ["↩️ Назад"]
            ])
            self.send_message(chat_id, support_text, reply_markup=keyboard, parse_mode="HTML")

        except Exception as e:
            error_msg = f"[ERROR] Обработка поддержки: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_command(self, chat_id, user_id, command, args):
        """Обработка админских команд"""
        try:
            if user_id not in ADMIN_IDS:
                self.send_message(chat_id, "⛔ У вас нет прав администратора.")
                return
            
            if command == "users":
                users = self.db.get_all_users()
                if users:
                    message = "👥 Список пользователей:\n\n"
                    for user in users[:20]:  # Показываем только первых 20
                        status_emoji = "✅" if user[2] == "active" else "⏳" if user[2] == "pending" else "❌"
                        message += f"{status_emoji} @{user[1] or 'no_username'} (ID: {user[0]}) - {user[2]}\n"
                else:
                    message = "👥 Пользователи не найдены"
                
                self.send_message(chat_id, message)
            
            elif command == "confirm" and args:
                try:
                    target_user_id = int(args[0])
                    user = self.db.get_user(target_user_id)
                    
                    if not user:
                        self.send_message(chat_id, "❌ Пользователь не найден")
                        return
                    
                    plan_key = user.get("plan")
                    if not plan_key or plan_key not in PLANS:
                        self.send_message(chat_id, "❌ У пользователя не выбран тариф или неверный тариф")
                        return
                    
                    plan = PLANS[plan_key]
                    now = datetime.now()
                    
                    # Активируем пользователя с правильным тарифом
                    start_date = datetime.now()
                    
                    if plan_key == "lifetime":
                        success = self.db.update_user_status(
                            target_user_id, 
                            "active", 
                            plan_key, 
                            start_date.isoformat(), 
                            None
                        )
                        end_date_str = "бессрочно"
                    else:
                        end_date = start_date + timedelta(days=plan["days"])
                        success = self.db.update_user_status(
                            target_user_id, 
                            "active", 
                            plan_key, 
                            start_date.isoformat(), 
                            end_date.isoformat()
                        )
                        end_date_str = end_date.strftime("%Y-%m-%d")
                    
                    if success:
                        username = user["username"] or "unknown"
                        plan_name = plan["name"]
                        
                        # Уведомляем пользователя
                        success_message = f"✅ Ваша подписка активирована: {plan_name}. Спасибо, что с нами!"
                        
                        self.send_message(target_user_id, success_message)
                        
                        # Логируем подтверждение в новом формате
                        active_until = end_date_str if end_date_str != "бессрочно" else "lifetime"
                        self.send_log(f"""[CONFIRMED]
user: @{username} (ID {target_user_id})
tariff: {plan_name}
active_until: {active_until}""")
                        
                        self.send_message(chat_id, f"✅ Пользователь {target_user_id} активирован с тарифом {plan_name}")
                    else:
                        self.send_message(chat_id, "❌ Ошибка при активации пользователя")
                        
                except ValueError:
                    self.send_message(chat_id, "❌ Неверный ID пользователя")
        
            elif command == "payments":
                payments = self.db.get_latest_payments(10)
                if payments:
                    message = "📊 Последние платежи:\n\n"
                    for payment in payments:
                        payment_id, username, txid, status, payment_method, plan, created_at = payment
                        txid_short = txid[:10] + "..." if txid and len(txid) > 10 else (txid or "N/A")
                        created_date = self.safe_parse_date(created_at)
                        created_date_str = created_date.strftime("%Y-%m-%d %H:%M") if created_date else "неизвестно"
                        
                        status_emoji = "✅" if status == "confirmed" else "⏳" if status == "pending" else "❌"
                        plan_name = PLANS.get(plan, {}).get("name", "Unknown") if plan else "Unknown"
                        method_name = "крипта" if payment_method == "crypto" else "Tribute"
                        
                        message += f"{status_emoji} #{payment_id} @{username or 'no_username'}\n"
                        message += f"План: {plan_name} | Метод: {method_name}\n"
                        message += f"TXID: {txid_short}\n"
                        message += f"Статус: {status}\n"
                        message += f"Дата: {created_date_str}\n\n"
                else:
                    message = "📊 Платежи не найдены"
                
                self.send_message(chat_id, message)
            
            elif command == "broadcast" and args:
                message = " ".join(args)
                active_users = self.db.get_active_users()
                sent_count = 0
                
                for user_id in active_users:
                    if self.send_message(user_id, message):
                        sent_count += 1
                    time.sleep(0.1)
                
                self.send_log(f"[BROADCAST] Message sent to {sent_count} users")
                self.send_message(chat_id, f"✅ Сообщение отправлено {sent_count} пользователям")
            
            elif command == "stats":
                stats = self.db.get_database_stats()
                message = f"""📊 Статистика бота:

👥 Пользователи: {stats['total_users']}
💰 Платежи: {stats['total_payments']}

Статусы пользователей:
"""
                for status, count in stats['users'].items():
                    message += f"• {status}: {count}\n"
                
                self.send_message(chat_id, message)
            
            elif command == "help":
                help_text = """
🔧 Админские команды:

/users - список всех пользователей
/confirm <user_id> - подтвердить оплату пользователя
/payments - отчет по последним платежам
/broadcast <сообщение> - отправить сообщение всем активным пользователям
/stats - статистика бота
/test_log - тестовое сообщение в лог-канал
/test_forward - тестовая пересылка сообщения
/test_db - проверка подключения к базе
/help - справка по командам

Примеры:
/confirm 123456789
/broadcast Важное объявление
                """
                self.send_message(chat_id, help_text)
            
            elif command == "test_log":
                self.send_log("[TEST] Тестовое сообщение от админа")
                self.send_message(chat_id, "✅ Тестовое сообщение отправлено в лог-канал")
            
            elif command == "test_forward":
                test_message = "🧪 Тестовое сообщение для проверки пересылки"
                if self.send_message(chat_id, test_message):
                    self.send_message(chat_id, "✅ Тестовая пересылка выполнена")
                else:
                    self.send_message(chat_id, "❌ Ошибка тестовой пересылки")
            
            elif command == "test_db":
                try:
                    # Проверяем подключение к базе
                    users_count = len(self.db.get_all_users())
                    self.send_message(chat_id, f"✅ База данных работает. Пользователей: {users_count}")
                except Exception as e:
                    self.send_message(chat_id, f"❌ Ошибка базы данных: {e}")
        
        except Exception as e:
            error_msg = f"[ERROR] Админская команда {command}: {e}"
            print(error_msg)
            self.send_log(error_msg)
            self.send_message(chat_id, "❌ Ошибка выполнения команды")
    
    def handle_admin_panel(self, chat_id, user_id):
        """Показать расширенную админ-панель"""
        try:
            if user_id not in ADMIN_IDS:
                self.send_message(chat_id, "⛔ У вас нет прав администратора.")
                return
            
            # Получаем быструю статистику
            stats = self.db.get_database_stats()
            
            admin_text = f"""⚙️ Панель администратора SignalBot Pro

📊 Быстрая статистика:
👥 Всего пользователей: {stats['total_users']}
💰 Активных подписок: {stats['active_users']}
⏳ Ожидают подтверждения: {stats['users'].get('pending', 0)}

Выберите действие:"""
            
            keyboard = self.create_inline_keyboard([
                [{"text": "👥 Пользователи", "callback_data": "admin_users"}, {"text": "💰 Платежи", "callback_data": "admin_payments"}],
                [{"text": "📊 Статистика", "callback_data": "admin_stats"}, {"text": "📢 Рассылка", "callback_data": "admin_broadcast"}],
                [{"text": "🔍 Поиск пользователя", "callback_data": "admin_search"}, {"text": "⚡ Быстрые действия", "callback_data": "admin_quick"}],
                [{"text": "📈 Аналитика", "callback_data": "admin_analytics"}, {"text": "⚙️ Настройки", "callback_data": "admin_settings"}]
            ])
            
            self.send_message(chat_id, admin_text, keyboard)
            
        except Exception as e:
            error_msg = f"[ERROR] Админ-панель: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def process_callback_query(self, callback_query):
        """Обработка callback запросов (нажатия кнопок)"""
        try:
            data = callback_query.get("data")
            chat_id = callback_query["message"]["chat"]["id"]
            user_id = callback_query["from"]["id"]
            
            # Обработка выбора плана (теперь не используется, так как убрали inline кнопки)
            if data.startswith("plan_"):
                plan_key = data.replace("plan_", "")
                self.handle_plan_selection(chat_id, user_id, plan_key)
            
            # Обработка админских callback
            elif data == "admin_users":
                if user_id in ADMIN_IDS:
                    self.handle_admin_users(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_payments":
                if user_id in ADMIN_IDS:
                    self.handle_admin_payments(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_stats":
                if user_id in ADMIN_IDS:
                    self.handle_admin_stats(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_broadcast":
                if user_id in ADMIN_IDS:
                    self.send_message(chat_id, "✉️ Введите сообщение для рассылки всем активным пользователям.")
                    self.db.set_user_state(user_id, "waiting_broadcast")
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_search":
                if user_id in ADMIN_IDS:
                    self.send_message(chat_id, "🔍 Введите username или ID пользователя для поиска:")
                    self.db.set_user_state(user_id, "waiting_user_search")
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_quick":
                if user_id in ADMIN_IDS:
                    self.handle_admin_quick_actions(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_analytics":
                if user_id in ADMIN_IDS:
                    self.handle_admin_analytics(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "admin_settings":
                if user_id in ADMIN_IDS:
                    self.handle_admin_settings(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            # Обработка подтверждения оплаты
            elif data.startswith("confirm_"):
                if user_id in ADMIN_IDS:
                    target_user_id = int(data.replace("confirm_", ""))
                    self.handle_confirm_payment(chat_id, user_id, target_user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            # Возврат в главное меню
            elif data == "back_main":
                self.handle_start(chat_id, user_id, callback_query["from"].get("username"))
            
            # Возврат в админ-панель
            elif data == "back_admin_panel":
                if user_id in ADMIN_IDS:
                    self.handle_admin_panel(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            # Быстрые действия
            elif data == "quick_confirm_all":
                if user_id in ADMIN_IDS:
                    self.handle_quick_confirm_all(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "quick_today_stats":
                if user_id in ADMIN_IDS:
                    self.handle_quick_today_stats(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "quick_update_statuses":
                if user_id in ADMIN_IDS:
                    self.handle_quick_update_statuses(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
            
            elif data == "quick_test_message":
                if user_id in ADMIN_IDS:
                    self.handle_quick_test_message(chat_id, user_id)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
        
        except Exception as e:
            error_msg = f"[ERROR] Callback query: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_users(self, chat_id, user_id):
        """Показать пользователей для админа"""
        try:
            users = self.db.get_users_for_admin(20)
            if not users:
                self.send_message(chat_id, "👥 Пользователи не найдены")
                return
            
            message = "👥 Последние пользователи:\n\n"
            for user_data in users:
                telegram_id, username, status, plan, start_date, end_date = user_data
                plan_name = PLANS.get(plan, {}).get("name", "none") if plan else "none"
                status_emoji = "✅" if status == "active" else "⏳" if status == "pending" else "❌"
                
                message += f"{status_emoji} @{username or 'no_username'} (ID: {telegram_id})\n"
                message += f"Статус: {status} | План: {plan_name}"
                
                if end_date and plan != "lifetime":
                    end_date_dt = self.safe_parse_date(end_date)
                    if end_date_dt:
                        end_date_str = end_date_dt.strftime("%Y-%m-%d")
                        message += f" | До: {end_date_str}"
                    else:
                        message += " | До: неизвестно"
                elif plan == "lifetime":
                    message += " | До: бессрочно"
                
                message += "\n\n"
            
            # Добавляем кнопку подтверждения для каждого пользователя
            keyboard_buttons = []
            for user_data in users:
                telegram_id, username, status, plan, start_date, end_date = user_data
                if status == "pending":
                    keyboard_buttons.append([{"text": f"✅ Подтвердить @{username or 'no_username'}", "callback_data": f"confirm_{telegram_id}"}])
            
            if keyboard_buttons:
                keyboard = self.create_inline_keyboard(keyboard_buttons)
                self.send_message(chat_id, message, keyboard)
            else:
                self.send_message(chat_id, message)
                
        except Exception as e:
            error_msg = f"[ERROR] Админ пользователи: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_payments(self, chat_id, user_id):
        """Показать платежи для админа"""
        try:
            payments = self.db.get_latest_payments(10)
            if not payments:
                self.send_message(chat_id, "💰 Платежи не найдены")
                return
            
            message = "💰 Последние платежи:\n\n"
            for payment in payments:
                payment_id, username, txid, status, payment_method, plan, created_at = payment
                txid_short = txid[:10] + "..." if txid and len(txid) > 10 else (txid or "N/A")
                created_date = self.safe_parse_date(created_at)
                created_date_str = created_date.strftime("%Y-%m-%d %H:%M") if created_date else "неизвестно"
                
                status_emoji = "✅" if status == "confirmed" else "⏳" if status == "pending" else "❌"
                plan_name = PLANS.get(plan, {}).get("name", "Unknown") if plan else "Unknown"
                method_name = "крипта" if payment_method == "crypto" else "Tribute"
                
                message += f"{status_emoji} @{username or 'no_username'}\n"
                message += f"План: {plan_name} | Метод: {method_name}\n"
                message += f"TXID: {txid_short}\n"
                message += f"Статус: {status} | Дата: {created_date_str}\n\n"
            
            self.send_message(chat_id, message)
            
        except Exception as e:
            error_msg = f"[ERROR] Админ платежи: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_stats(self, chat_id, user_id):
        """Показать статистику для админа"""
        try:
            stats = self.db.get_database_stats()
            
            message = f"""📊 Статистика подписок:

👥 Всего пользователей: {stats['total_users']}
💰 Активных подписок: {stats['active_users']}
⏳ Ожидают подтверждения: {stats['users'].get('pending', 0)}
❌ Истекших: {stats['users'].get('expired', 0)}

По тарифам:"""
            
            for plan, count in stats['plans'].items():
                if plan != 'none':
                    plan_name = PLANS.get(plan, {}).get("name", plan)
                    message += f"\n• {plan_name}: {count}"
            
            self.send_message(chat_id, message)
            
        except Exception as e:
            error_msg = f"[ERROR] Админ статистика: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_confirm_payment(self, chat_id, user_id, target_user_id):
        """Подтвердить оплату пользователя"""
        try:
            target_user = self.db.get_user(target_user_id)
            if not target_user:
                self.send_message(chat_id, "❌ Пользователь не найден")
                return
            
            plan_key = target_user["plan"]
            plan = PLANS.get(plan_key)
            if not plan:
                self.send_message(chat_id, "❌ Неверный план пользователя")
                return
            
            # Активируем пользователя
            start_date = datetime.now()
            if plan_key == "lifetime":
                success = self.db.update_user_status(
                    target_user_id, 
                    "active", 
                    plan_key, 
                    start_date.isoformat(), 
                    None
                )
                end_date_str = "бессрочно"
            else:
                end_date = start_date + timedelta(days=plan["days"])
                success = self.db.update_user_status(
                    target_user_id, 
                    "active", 
                    plan_key, 
                    start_date.isoformat(), 
                    end_date.isoformat()
                )
                end_date_str = end_date.strftime("%Y-%m-%d")
            
            if success:
                username = target_user["username"] or "unknown"
                plan_name = plan["name"]
                
                # Уведомляем пользователя
                success_message = f"✅ Ваша подписка активирована: {plan_name}. Спасибо, что с нами!"
                
                self.send_message(target_user_id, success_message)
                
                # Логируем подтверждение в новом формате
                active_until = end_date_str if end_date_str != "бессрочно" else "lifetime"
                self.send_log(f"""[CONFIRMED]
user: @{username} (ID {target_user_id})
tariff: {plan_name}
active_until: {active_until}""")
                
                self.send_message(chat_id, f"✅ Пользователь {target_user_id} активирован с тарифом {plan_name}")
            else:
                self.send_message(chat_id, "❌ Ошибка при активации пользователя")
                
        except Exception as e:
            error_msg = f"[ERROR] Подтверждение оплаты: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_quick_actions(self, chat_id, user_id):
        """Быстрые действия для админа"""
        try:
            quick_text = """⚡ Быстрые действия

Выберите действие:"""
            
            keyboard = self.create_inline_keyboard([
                [{"text": "✅ Подтвердить все pending", "callback_data": "quick_confirm_all"}],
                [{"text": "📊 Статистика за сегодня", "callback_data": "quick_today_stats"}],
                [{"text": "🔄 Обновить статусы", "callback_data": "quick_update_statuses"}],
                [{"text": "📤 Тестовое сообщение", "callback_data": "quick_test_message"}],
                [{"text": "↩️ Назад в панель", "callback_data": "back_admin_panel"}]
            ])
            
            self.send_message(chat_id, quick_text, keyboard)
            
        except Exception as e:
            error_msg = f"[ERROR] Быстрые действия: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_analytics(self, chat_id, user_id):
        """Расширенная аналитика для админа"""
        try:
            stats = self.db.get_database_stats()
            
            # Получаем статистику по дням
            daily_stats = self.db.get_daily_stats()
            
            analytics_text = f"""📈 Расширенная аналитика

📊 Общая статистика:
👥 Всего пользователей: {stats['total_users']}
💰 Активных подписок: {stats['active_users']}
⏳ Ожидают подтверждения: {stats['users'].get('pending', 0)}
❌ Истекших: {stats['users'].get('expired', 0)}

📅 Статистика за сегодня:
👤 Новые пользователи: {daily_stats['new_users']}
💰 Новых оплат: {daily_stats['new_payments']}
❌ Истекших подписок: {daily_stats['expired_users']}

📈 По тарифам:"""
            
            # Добавляем статистику по планам
            for plan, count in stats['plans'].items():
                if plan != 'none' and count > 0:
                    plan_name = PLANS.get(plan, {}).get("name", plan)
                    analytics_text += f"\n• {plan_name}: {count}"
            
            self.send_message(chat_id, analytics_text)
            
        except Exception as e:
            error_msg = f"[ERROR] Аналитика: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_admin_settings(self, chat_id, user_id):
        """Настройки админ-панели"""
        try:
            settings_text = f"""⚙️ Настройки бота

🔧 Текущие настройки:
📱 Токен бота: {TOKEN[:10]}...{TOKEN[-10:]}
👑 Админы: {len(ADMIN_IDS)} пользователей
💰 Крипто-адрес: {CRYPTO_ADDRESS[:10]}...{CRYPTO_ADDRESS[-10:]}
📊 Канал сигналов: {SIGNAL_CHANNEL_ID}
📝 Лог-канал: {LOG_CHANNEL_ID}

🔍 Для изменения настроек отредактируйте config.py"""
            
            self.send_message(chat_id, settings_text)
            
        except Exception as e:
            error_msg = f"[ERROR] Настройки: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_user_search(self, chat_id, user_id, search_query):
        """Поиск пользователя по username или ID"""
        try:
            # Пытаемся найти по ID (если введено число)
            try:
                target_id = int(search_query)
                user = self.db.get_user(target_id)
                if user:
                    self.send_user_info(chat_id, user_id, user)
                    return
            except ValueError:
                pass
            
            # Ищем по username
            users = self.db.get_all_users()
            found_users = []
            
            for user_data in users:
                username = user_data[1]  # username в индексе 1
                if username and search_query.lower() in username.lower():
                    found_users.append(user_data)
            
            if found_users:
                if len(found_users) == 1:
                    # Если найден один пользователь, показываем его информацию
                    user_data = found_users[0]
                    user_info = {
                        'telegram_id': user_data[0],
                        'username': user_data[1],
                        'status': user_data[2],
                        'plan': user_data[3],
                        'start_date': user_data[4],
                        'end_date': user_data[5],
                        'joined_at': user_data[6],
                        'last_seen': user_data[7]
                    }
                    self.send_user_info(chat_id, user_id, user_info)
                else:
                    # Если найдено несколько пользователей, показываем список
                    message = f"🔍 Найдено {len(found_users)} пользователей:\n\n"
                    for i, user_data in enumerate(found_users[:10]):  # Показываем максимум 10
                        telegram_id, username, status = user_data[0], user_data[1], user_data[2]
                        status_emoji = "✅" if status == "active" else "⏳" if status == "pending" else "❌"
                        message += f"{i+1}. {status_emoji} @{username or 'no_username'} (ID: {telegram_id})\n"
                    
                    if len(found_users) > 10:
                        message += f"\n... и еще {len(found_users) - 10} пользователей"
                    
                    message += "\n\n💡 Введите точный ID для получения подробной информации"
                    self.send_message(chat_id, message)
            else:
                self.send_message(chat_id, f"❌ Пользователь '{search_query}' не найден.\n\n💡 Попробуйте ввести точный username или ID пользователя.")
                
        except Exception as e:
            error_msg = f"[ERROR] Поиск пользователя: {e}"
            print(error_msg)
            self.send_log(error_msg)
            self.send_message(chat_id, "❌ Ошибка при поиске пользователя")
    
    def send_user_info(self, chat_id, admin_id, user):
        """Отправка подробной информации о пользователе"""
        try:
            plan_name = PLANS.get(user["plan"], {}).get("name", "Unknown") if user["plan"] else "None"
            
            info_text = f"""👤 Информация о пользователе

🆔 ID: {user['telegram_id']}
👤 Username: @{user['username'] or 'не указан'}
📊 Статус: {user['status']}
💎 План: {plan_name}
📅 Регистрация: {user['joined_at'][:10] if user['joined_at'] else 'неизвестно'}
🕐 Последняя активность: {user['last_seen'][:16] if user['last_seen'] else 'неизвестно'}"""
            
            if user["start_date"]:
                info_text += f"\n🚀 Начало подписки: {user['start_date'][:10]}"
            
            if user["end_date"]:
                if user["plan"] == "lifetime":
                    info_text += f"\n♾️ Подписка: бессрочная"
                else:
                    end_date_dt = self.safe_parse_date(user["end_date"])
                    if end_date_dt:
                        end_date_str = end_date_dt.strftime("%d.%m.%Y")
                        days_left = (end_date_dt - datetime.now()).days
                        info_text += f"\n📅 Подписка до: {end_date_str}"
                        info_text += f"\n⏳ Осталось дней: {days_left}"
                    else:
                        info_text += f"\n📅 Подписка до: неизвестно"
            
            # Создаем кнопки для управления пользователем
            keyboard_buttons = []
            
            if user["status"] == "pending":
                keyboard_buttons.append([{"text": f"✅ Подтвердить @{user['username'] or 'user'}", "callback_data": f"confirm_{user['telegram_id']}"}])
            
            if user["status"] in ["active", "expired", "none"]:
                keyboard_buttons.append([{"text": f"📤 Написать сообщение", "callback_data": f"message_{user['telegram_id']}"}])
            
            keyboard_buttons.append([{"text": "↩️ Назад в панель", "callback_data": "back_admin_panel"}])
            
            if keyboard_buttons:
                keyboard = self.create_inline_keyboard(keyboard_buttons)
                self.send_message(chat_id, info_text, keyboard)
            else:
                self.send_message(chat_id, info_text)
                
        except Exception as e:
            error_msg = f"[ERROR] Отправка информации о пользователе: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_quick_confirm_all(self, chat_id, user_id):
        """Быстрое подтверждение всех pending пользователей"""
        try:
            users = self.db.get_all_users()
            pending_users = [u for u in users if u[2] == "pending"]  # status в индексе 2
            
            if not pending_users:
                self.send_message(chat_id, "✅ Нет пользователей со статусом 'pending' для подтверждения.")
                return
            
            confirmed_count = 0
            for user_data in pending_users:
                try:
                    target_user_id = user_data[0]  # telegram_id
                    target_user = self.db.get_user(target_user_id)
                    
                    if target_user and target_user["plan"]:
                        plan_key = target_user["plan"]
                        plan = PLANS.get(plan_key)
                        
                        if plan:
                            start_date = datetime.now()
                            if plan_key == "lifetime":
                                success = self.db.update_user_status(
                                    target_user_id, 
                                    "active", 
                                    plan_key, 
                                    start_date.isoformat(), 
                                    None
                                )
                                end_date_str = "бессрочно"
                            else:
                                end_date = start_date + timedelta(days=plan["days"])
                                success = self.db.update_user_status(
                                    target_user_id, 
                                    "active", 
                                    plan_key, 
                                    start_date.isoformat(), 
                                    end_date.isoformat()
                                )
                                end_date_str = end_date.strftime("%Y-%m-%d")
                            
                            if success:
                                confirmed_count += 1
                                
                                # Уведомляем пользователя
                                username = target_user["username"] or "unknown"
                                plan_name = plan["name"]
                                
                                success_message = f"✅ Ваша подписка активирована: {plan_name}. Спасибо, что с нами!"
                                
                                self.send_message(target_user_id, success_message)
                                
                except Exception as e:
                    print(f"[ERROR] Подтверждение пользователя {target_user_id}: {e}")
            
            self.send_message(chat_id, f"✅ Подтверждено {confirmed_count} из {len(pending_users)} пользователей.")
            self.send_log(f"[QUICK CONFIRM] Подтверждено {confirmed_count} пользователей с тарифами")
            
        except Exception as e:
            error_msg = f"[ERROR] Быстрое подтверждение: {e}"
            print(error_msg)
            self.send_log(error_msg)
            self.send_message(chat_id, "❌ Ошибка при подтверждении пользователей")
    
    def handle_quick_today_stats(self, chat_id, user_id):
        """Быстрая статистика за сегодня"""
        try:
            daily_stats = self.db.get_daily_stats()
            
            stats_text = f"""📊 Статистика за сегодня

👤 Новые пользователи: {daily_stats['new_users']}
💰 Новых оплат: {daily_stats['new_payments']}
❌ Истекших подписок: {daily_stats['expired_users']}
📈 Активных подписок: {daily_stats['active_users']}

📅 Дата: {datetime.now().strftime('%d.%m.%Y')}"""
            
            self.send_message(chat_id, stats_text)
            
        except Exception as e:
            error_msg = f"[ERROR] Статистика за сегодня: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_quick_update_statuses(self, chat_id, user_id):
        """Быстрое обновление статусов пользователей"""
        try:
            # Обновляем просроченные подписки
            expired_users = self.db.get_expired_users()
            updated_count = 0
            
            for user in expired_users:
                user_id_expired = user["telegram_id"]
                username = user["username"]
                
                self.db.update_user_status(user_id_expired, "expired")
                self.send_message(user_id_expired, "❌ Ваша подписка истекла. Для продолжения получения сигналов продлите подписку.")
                updated_count += 1
                
                self.send_log(f"[EXPIRED] user: @{username} (ID: {user_id_expired})")
            
            self.send_message(chat_id, f"🔄 Обновлено статусов: {updated_count} пользователей")
            self.send_log(f"[QUICK UPDATE] Обновлено {updated_count} статусов")
            
        except Exception as e:
            error_msg = f"[ERROR] Обновление статусов: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def handle_quick_test_message(self, chat_id, user_id):
        """Быстрая отправка тестового сообщения"""
        try:
            test_message = f"""🧪 Тестовое сообщение от администратора

⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
👑 От: Администратор SignalBot Pro

✅ Бот работает корректно!"""
            
            self.send_message(chat_id, test_message)
            self.send_log(f"[QUICK TEST] Тестовое сообщение отправлено админом {user_id}")
            
        except Exception as e:
            error_msg = f"[ERROR] Тестовое сообщение: {e}"
            print(error_msg)
            self.send_log(error_msg)

    def process_message(self, message):
        """Обработка текстовых сообщений"""
        try:
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            username = message["from"].get("username")
            text = message.get("text", "")
            
            # Проверяем состояние пользователя
            user_state = self.db.get_user_state(user_id)
        
            if text.startswith("/start"):
                self.handle_start(chat_id, user_id, username)
            
            elif text == "📈 Получать сигналы":
                self.handle_get_signals(chat_id, user_id)
            
            elif text == "🆘 Помощь":
                self.handle_help_faq(chat_id)
            
            elif text == "💰 Оплата":
                self.handle_payment_start(chat_id, user_id)
            
            elif text == "✅ Я оплатил":
                self.handle_payment_done(chat_id, user_id)
            
            # Обработка выбора тарифов
            elif text == "1 месяц — 39 USDT":
                self.handle_plan_selection(chat_id, user_id, "1m")
            elif text == "3 месяца — 99 USDT":
                self.handle_plan_selection(chat_id, user_id, "3m")
            elif text == "Пожизненно — 239 USDT":
                self.handle_plan_selection(chat_id, user_id, "lifetime")
            
            # Обработка выбора методов оплаты
            elif text == "💰 Оплатить криптой (TRC20)":
                # Определяем план из состояния пользователя
                if user_state and user_state.startswith("payment_method_"):
                    plan_key = user_state.replace("payment_method_", "")
                    self.handle_crypto_payment(chat_id, user_id, plan_key)
                else:
                    self.send_message(chat_id, "❌ Сначала выберите тариф")
            
            elif text == "⚡ Оплатить через Tribute":
                # Определяем план из состояния пользователя
                if user_state and user_state.startswith("payment_method_"):
                    plan_key = user_state.replace("payment_method_", "")
                    self.handle_tribute_payment(chat_id, user_id, plan_key)
                else:
                    self.send_message(chat_id, "❌ Сначала выберите тариф")
            
            elif text == "📸 Отправить скрин":
                # Если пользователь нажал кнопку "Отправить скрин", просим отправить фото
                self.send_message(chat_id, "📸 Отправьте скриншот перевода:")
            
            elif text == "↩️ Назад":
                keyboard = self.create_reply_keyboard([
                    ["📈 Получать сигналы"],
                    ["💰 Оплата"],
                    ["ℹ️ Мой статус"],
                    ["🧾 Поддержка", "🆘 Помощь"]
                ])
                self.send_message(chat_id, "Вы вернулись в главное меню.", keyboard)
            
            elif text == "ℹ️ Мой статус" or text.startswith("/status"):
                self.handle_status(chat_id, user_id)
        
            elif text == "🧾 Поддержка":
                self.handle_support(chat_id)
            
            elif text.startswith("/users"):
                self.handle_admin_command(chat_id, user_id, "users", [])
            
            elif text.startswith("/confirm"):
                args = text.split()[1:] if len(text.split()) > 1 else []
                self.handle_admin_command(chat_id, user_id, "confirm", args)
            
            elif text.startswith("/payments"):
                self.handle_admin_command(chat_id, user_id, "payments", [])
            
            elif text.startswith("/broadcast"):
                args = text.split()[1:] if len(text.split()) > 1 else []
                self.handle_admin_command(chat_id, user_id, "broadcast", args)
            
            elif text.startswith("/stats"):
                self.handle_admin_command(chat_id, user_id, "stats", [])
            
            elif text.startswith("/help"):
                self.handle_admin_command(chat_id, user_id, "help", [])
            
            elif text.startswith("/test_log"):
                self.handle_admin_command(chat_id, user_id, "test_log", [])
            
            elif text.startswith("/test_forward"):
                self.handle_admin_command(chat_id, user_id, "test_forward", [])
            
            elif text.startswith("/test_db"):
                self.handle_admin_command(chat_id, user_id, "test_db", [])
            
            elif text.startswith("/admin"):
                self.handle_admin_panel(chat_id, user_id)
            
            elif text.startswith("/panel"):
                self.handle_admin_panel(chat_id, user_id)
        
            # Обработка состояний пользователя
            elif user_state == "waiting_txid":
                self.handle_txid(chat_id, user_id, username, text)
            
            # Обработка рассылки для админа
            elif user_state == "waiting_broadcast":
                if user_id in ADMIN_IDS:
                    active_users = self.db.get_active_users()
                    sent_count = 0
                    
                    for target_user_id in active_users:
                        try:
                            if self.send_message(target_user_id, text):
                                sent_count += 1
                            time.sleep(0.1)
                        except Exception as e:
                            print(f"[ERROR] Рассылка пользователю {target_user_id}: {e}")
                    
                    self.send_log(f"[BROADCAST] Сообщение доставлено {sent_count} пользователям")
                    self.send_message(chat_id, f"✅ Сообщение отправлено {sent_count} пользователям")
                    self.db.set_user_state(user_id, None)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
                    self.db.set_user_state(user_id, None)
            
            # Обработка поиска пользователя для админа
            elif user_state == "waiting_user_search":
                if user_id in ADMIN_IDS:
                    self.handle_user_search(chat_id, user_id, text)
                    self.db.set_user_state(user_id, None)
                else:
                    self.send_message(chat_id, "⛔ У вас нет прав администратора.")
                    self.db.set_user_state(user_id, None)
            
            # Обработка скриншотов
            elif message.get("photo"):
                if (user_state and 
                    (user_state == "waiting_screenshot" or 
                     user_state.startswith("waiting_screenshot_crypto_") or 
                     user_state.startswith("waiting_screenshot_tribute_"))):
                    file_id = message["photo"][-1]["file_id"]  # Берем самое большое изображение
                    self.handle_screenshot(chat_id, user_id, username, file_id, user_state)
        
        except Exception as e:
            error_msg = f"[ERROR] Обработка сообщения: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def check_signal_channel(self, updates):
        """Проверка новых сообщений в сигнальном канале"""
        try:
            new_messages = []
            for update in updates:
                message = update.get("channel_post") or update.get("edited_channel_post")
                if not message:
                    continue
                if message.get("chat", {}).get("id") != SIGNAL_CHANNEL_ID:
                    continue

                message_id = message.get("message_id")
                if self.last_message_id is None or message_id > self.last_message_id:
                    self.last_message_id = message_id
                    new_messages.append(message)

            if not new_messages:
                return

            # Получаем активных пользователей + админов
            active_users = self.db.get_active_users()
            all_recipients = list(set(active_users + ADMIN_IDS))

            for message in new_messages:
                message_id = message.get("message_id")
                forwarded_count = 0
                for user_id in all_recipients:
                    try:
                        # Пересылаем любое сообщение (включая фото, видео, документы)
                        ok = self.forward_message(SIGNAL_CHANNEL_ID, user_id, message_id)
                        if ok:
                            forwarded_count += 1
                        time.sleep(0.05)
                    except Exception as e:
                        print(f"[ERROR] Пересылка сигнала {message_id} -> {user_id}: {e}")

                if forwarded_count > 0:
                    self.send_log(f"[SIGNAL FORWARDED] message_id={message_id}, users={forwarded_count}")

        except Exception as e:
            self.send_log(f"[ERROR] check_signal_channel: {e}")

    
    def check_subscriptions(self):
        """Проверка подписок на истечение"""
        try:
            # Проверяем подписки, которые истекают завтра
            tomorrow = datetime.now() + timedelta(days=1)
            expiring_users = self.db.get_expiring_users(tomorrow)
            
            for user in expiring_users:
                user_id = user["telegram_id"]
                username = user["username"]
                end_date_dt = self.safe_parse_date(user.get("end_date"))
                if end_date_dt:
                    end_date_str = end_date_dt.strftime("%d.%m.%Y")
                    self.send_message(user_id, f"⚠️ Ваша подписка истекет завтра ({end_date_str}). Продлите подписку для продолжения получения сигналов.")
                    self.send_log(f"[REMINDER] user: @{username} (ID: {user_id}), expires: {end_date_str}")
                else:
                    self.send_message(user_id, f"⚠️ Ваша подписка скоро истекает. Продлите подписку для продолжения получения сигналов.")
                    self.send_log(f"[REMINDER] user: @{username} (ID: {user_id}), expires: неизвестно")
            
            # Проверяем просроченные подписки
            expired_users = self.db.get_expired_users()
            
            for user in expired_users:
                user_id = user["telegram_id"]
                username = user["username"]
                
                self.db.update_user_status(user_id, "expired")
                self.send_message(user_id, "❌ Ваша подписка истекла. Для продолжения получения сигналов продлите подписку.")
                self.send_log(f"[EXPIRED] user: @{username} (ID: {user_id})")
                
        except Exception as e:
            error_msg = f"[ERROR] Проверка подписок: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def create_daily_backup(self):
        """Создание ежедневного резервного копирования"""
        try:
            today = datetime.now().date()
            
            # Проверяем, не создавали ли уже бэкап сегодня
            if self.last_backup_date == today:
                return
            
            if self.db.create_backup():
                self.last_backup_date = today
                self.send_log("[BACKUP] Резервная копия базы данных создана")
            else:
                self.send_log("[ERROR] Не удалось создать резервную копию")
                
        except Exception as e:
            error_msg = f"[ERROR] Резервное копирование: {e}"
            print(error_msg)
            self.send_log(error_msg)
    
    def subscription_checker_thread(self):
        """Поток для проверки подписок"""
        while self.running:
            try:
                self.check_subscriptions()
                time.sleep(86400)  # Проверяем раз в день
            except Exception as e:
                error_msg = f"[ERROR] Поток проверки подписок: {e}"
                print(error_msg)
                self.send_log(error_msg)
                time.sleep(3600)  # При ошибке ждем час
    
    def backup_thread(self):
        """Поток для ежедневного резервного копирования и отчетов"""
        last_report_date = None
        
        while self.running:
            try:
                today = datetime.now().date()
                
                # Ежедневный отчет
                if last_report_date != today:
                    self.send_daily_report()
                    last_report_date = today
                
                # Резервное копирование
                self.create_daily_backup()
                
                time.sleep(3600)  # Проверяем каждый час
            except Exception as e:
                error_msg = f"[ERROR] Поток резервного копирования: {e}"
                print(error_msg)
                self.send_log(error_msg)
                time.sleep(3600)
    
    def send_daily_report(self):
        """Отправка ежедневного отчета"""
        try:
            stats = self.db.get_daily_stats()
            
            report_message = f"""📆 Ежедневный отчёт

👤 Новые пользователи: {stats['new_users']}
💰 Новых оплат: {stats['new_payments']}
❌ Истекших подписок: {stats['expired_users']}
📈 Активных подписок: {stats['active_users']}"""
            
            self.send_log(report_message)
            
        except Exception as e:
            print(f"[ERROR] Ежедневный отчет: {e}")
    
    def run(self):
        """Основной цикл бота - единый polling для всех операций"""
        self.running = True
        
        # Запускаем только потоки для фоновых задач
        subscription_thread = threading.Thread(target=self.subscription_checker_thread)
        subscription_thread.daemon = True
        subscription_thread.start()
        
        backup_thread = threading.Thread(target=self.backup_thread)
        backup_thread.daemon = True
        backup_thread.start()
        
        offset = None
        last_check_time = time.time()
        
        print("[BOT] Запущен и готов")
        self.send_log("[BOT] Запущен и готов")
        
        while self.running:
            try:
                # Получаем обновления
                updates = self.get_updates(offset, timeout=30)
                
                # Проверяем сигнальный канал каждые 10 секунд
                current_time = time.time()
                if current_time - last_check_time >= 10:
                    self.check_signal_channel(updates)
                    last_check_time = current_time
                
                # Обрабатываем все обновления
                for update in updates:
                    offset = update["update_id"] + 1
                    
                    if "message" in update:
                        self.process_message(update["message"])
                    
                    elif "callback_query" in update:
                        self.process_callback_query(update["callback_query"])
                        
                        # Безопасно отвечаем на callback, чтобы Telegram не ругался
                        try:
                            callback_query_id = update["callback_query"]["id"]
                            self.send_request("answerCallbackQuery", {"callback_query_id": callback_query_id})
                        except Exception:
                            pass
                
            except KeyboardInterrupt:
                print("\n[BOT] Остановка...")
                self.running = False
                self.send_log("[BOT] Остановлен")
                break
            
            except requests.exceptions.Timeout:
                # Игнорируем timeout ошибки
                time.sleep(1)
                continue
            
            except requests.exceptions.RequestException as e:
                # Игнорируем сетевые ошибки
                if "409" not in str(e) and "timeout" not in str(e).lower():
                    print(f"[NETWORK ERROR] {e}")
                time.sleep(3)
                continue
            
            except Exception as e:
                error_msg = f"[ERROR] Основной цикл: {e}"
                print(error_msg)
                # Не логируем все ошибки, чтобы не спамить канал
                if "no such column" not in str(e).lower():
                    self.send_log(error_msg)
                time.sleep(3)

if __name__ == "__main__":
    bot = SignalBot()
    bot.run()
