import csv
import dataclasses
import datetime
import io
import json
import os
import traceback

import tabulate
import telebot
from telebot import types


class Config:
    def __init__(self, env_name):
        c = os.getenv(env_name)
        if c is None:
            print(env_name + ' not found')
            exit(1)

        j = json.loads(c)
        self.token = j['token']
        self.chat = j['chat']
        self.groups = j['groups']
        self.users = [i for l in j['groups'].values() for i in l]
        self.dbfile = j['dbfile']


config = Config('MB_CONF')
bot = telebot.TeleBot(config.token)


def log(data: dict):
    print(' | '.join([datetime.datetime.now().strftime("%d.%m.%Y %H:%M")] + [f'{k}:{v}' for k, v in data.items()]))


class CurrentBuy:
    def __init__(self, payer, buy, price, user):
        self.payer: str = payer
        self.buy: str = buy
        self.price: float = price
        self.user: int = user


class State:
    last_add_user = None
    cur = CurrentBuy('', '', 0, 0)


class DB:
    @dataclasses.dataclass
    class Obj:
        payer: str
        buy: str
        price: float
        price_parts: dict[str, float]
        date: datetime.datetime

        @property
        def sorted_parts(self):
            return [self.price_parts[g] for g in config.groups]

        def to_csv_list(self):
            return [self.payer, self.buy, self.price, *self.sorted_parts, self.date.timestamp()]

        @staticmethod
        def from_csv_list(l):
            return DB.Obj(payer=str(l[0]), buy=str(l[1]), price=float(l[2]),
                          price_parts=dict(zip(config.groups.keys(), map(float, l[3:-1]))),
                          date=datetime.datetime.fromtimestamp(float(l[-1])))

    def __init__(self, filename):
        self.filename = filename

    def get_all(self):
        with open(self.filename, 'r', encoding='utf8') as csvfile:
            reader = csv.reader(csvfile, delimiter='|')
            return [DB.Obj.from_csv_list(row) for row in reader]

    def put_obj(self, obj: Obj):
        with open(self.filename, 'a', newline='', encoding='utf8') as csvfile:
            new_line = csv.writer(csvfile, delimiter='|', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            new_line.writerow(obj.to_csv_list())

    def clear(self):
        os.rename(self.filename, f'{self.filename}.{datetime.datetime.now().timestamp():.0f}')
        with open(self.filename, 'w'):
            pass

    def rm_last(self):
        with open(self.filename, 'r+', encoding='utf8') as f:
            r = f.read()
            idx = r.rfind('\n', 0, -1)
            if idx >= 0:
                f.truncate(len(r[:idx + 1].encode('utf8')))
            elif r.rfind('\n') >= 0:
                f.truncate(0)


db = DB(config.dbfile)


@bot.message_handler(commands=['start'])
def send_welcome(message):
    result = is_chat(message.from_user.id, message.chat.id)
    if result:
        bot.send_message(message.chat.id, "Hi! I'm a money_bot"u'\U0001F4B8')


@bot.message_handler(commands=['help'])
def send_help(message):
    result = is_chat(message.from_user.id, message.chat.id)
    if result:
        bot.send_message(message.chat.id, f"help\U0001F64C\n\ngroups: {','.join(config.groups)}\n\n/add\
 - function for addition new buy\nformat: buy price\nformat for buying not in half: name_of_payer_1 price_1\
 name_of_payer_2 price_2\n\n/summary - balances for groups\n\n/table - \
 table with payers, buys, prices and price for everyone\n\n/table_min - \
 table with payers, buys and prices\n\n/delete - delete last row\
 \n\n/clean - clean table")


def is_chat(user_id, chat_id):
    if user_id in config.users and chat_id == config.chat:
        return True
    else:
        bot.send_message(chat_id, 'this bot doesn\'t work for you\U0001F595')
        return False


@bot.message_handler(commands=['add'])
def add(message):
    result = is_chat(message.from_user.id, message.chat.id)
    if result:
        State.cur.user = message.from_user.id
        bot.send_message(message.chat.id, "enter add parameters\nformat: name_of_buy price")
        bot.register_next_step_handler(message, add_func)


def add_func(message):
    log({'cmd': 'add',
         'username': message.from_user.username,
         'first_name': message.from_user.first_name,
         'text': message.text})
    if int(State.cur.user) != int(message.from_user.id):
        bot.register_next_step_handler(message, add_func)
    else:

        try:
            mes = str(message.text)
            request = mes.split()
            payer = None
            for group, ids in config.groups.items():
                if message.from_user.id in ids:
                    payer = group
                    break

            buy = ' '.join(request[:-1])
            price = float(request[-1])

            if isinstance(payer, str) and isinstance(buy, str):
                State.cur = CurrentBuy(payer, buy, price, message.from_user.id)

                markup = types.InlineKeyboardMarkup()
                button_half = types.InlineKeyboardButton(text='in half', callback_data='inhalf')
                button_over = types.InlineKeyboardButton(text='other', callback_data='other')
                markup.add(button_half, button_over)
                bot.send_message(message.chat.id, text='half payment or not?', reply_markup=markup)

            else:
                bot.send_message(message.chat.id, 'wrong request')
        except:
            traceback.print_exc()
            bot.send_message(message.chat.id, 'wrong request')


def func_other(msg: telebot.types.Message):
    try:
        if int(State.cur.user) != int(msg.from_user.id):
            bot.register_next_step_handler(msg, func_other)
        else:
            req_parts = msg.text.split(' ')
            if len(req_parts) / 2 != len(config.groups):
                raise ValueError('invalid format')
            price_parts = {req_parts[i]: float(req_parts[i + 1]) for i in range(0, len(req_parts), 2)}
            for k in price_parts:
                if k not in config.groups:
                    raise ValueError('no such group')

            if sum(price_parts.values()) == State.cur.price:
                db.put_obj(
                    DB.Obj(State.cur.payer, State.cur.buy, State.cur.price, price_parts, datetime.datetime.now()))
                State.last_add_user = State.cur.user
                bot.send_message(msg.chat.id, 'your buy was written')
            else:
                bot.send_message(msg.chat.id, 'prices aren\'t equal sum\nplease write price')
                bot.register_next_step_handler(msg, func_other)
    except:
        traceback.print_exc()
        bot.send_message(msg.chat.id, 'wrong request')


def filter(call):
    return call.from_user.id == State.cur.user


@bot.callback_query_handler(lambda call: call.data in ['inhalf', 'other'] and filter(call))
def callback_inline(call):
    try:
        if call.message:
            if call.data == 'inhalf':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                price_parts = {g: State.cur.price / len(config.groups) for g in config.groups}
                db.put_obj(
                    DB.Obj(State.cur.payer, State.cur.buy, State.cur.price, price_parts, datetime.datetime.now()))
                State.last_add_user = State.cur.user
                bot.send_message(call.message.chat.id, 'your buy was written')
            elif call.data == 'other':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                mes = bot.send_message(call.message.chat.id,
                                       "please write price for everyone\n"
                                       "format: 1_payer 1_price 2_payer 2_price")
                bot.register_next_step_handler(mes, func_other)
    except:
        traceback.print_exc()
        bot.send_message(call.message.chat.id, 'wrong request')


@bot.message_handler(commands=['summary'])
def summary(message):
    try:
        result = is_chat(message.from_user.id, message.chat.id)
        if result:
            pays = {g: 0 for g in config.groups}
            spends = {g: 0 for g in config.groups}
            for obj in db.get_all():
                pays[obj.payer] += obj.price
                for g, p in obj.price_parts.items():
                    spends[g] += p
            balances = {g: pays[g] - spends[g] for g in config.groups}
            balances_str = "\n".join([f'{g} = {b}' for g, b in balances.items()])
            bot.send_message(message.chat.id, f'Balances:\n`{balances_str}`', parse_mode='Markdown')
    except:
        traceback.print_exc()
        bot.send_message(message.chat.id, 'wrong request')


@bot.message_handler(commands=['table'])
def table(message):
    try:
        result = is_chat(message.from_user.id, message.chat.id)
        if result:
            table_summary = [['payer', 'buy', 'price', *list(config.groups), 'date']]
            for obj in db.get_all():
                table_summary.append([obj.payer, obj.buy, obj.price, *list(obj.price_parts.values()),
                                      obj.date.strftime("%d.%m.%Y %H:%M")])
            file = io.StringIO(
                tabulate.tabulate(table_summary, headers="firstrow", showindex="always", tablefmt="orgtbl"))
            file.name = 'table.txt'
            bot.send_document(message.chat.id,
                              document=telebot.types.InputFile(file))
    except:
        traceback.print_exc()
        bot.send_message(message.chat.id, 'wrong request')


@bot.message_handler(commands=['table_min'])
def table_min(message):
    try:
        result = is_chat(message.from_user.id, message.chat.id)
        if result:
            table_summary = [['payer', 'buy', 'price']]
            for obj in db.get_all():
                table_summary.append([obj.payer, obj.buy, obj.price])
            bot.send_message(message.chat.id,
                             '`' + tabulate.tabulate(table_summary, headers="firstrow", tablefmt="orgtbl") + '`',
                             parse_mode='Markdown')
    except:
        traceback.print_exc()
        bot.send_message(message.chat.id, 'wrong request')


@bot.message_handler(commands=['clean'])
def clean(message):
    State.cur.user = message.from_user.id
    markup = types.InlineKeyboardMarkup()
    button_yes = types.InlineKeyboardButton(text='yes', callback_data='yes')
    button_no = types.InlineKeyboardButton(text='no', callback_data='no')
    markup.add(button_yes, button_no)
    bot.send_message(message.chat.id, text='are you sure you want to clean table?', reply_markup=markup)


@bot.callback_query_handler(lambda call: call.data in ['yes', 'no'] and filter(call))
def func_clean(call):
    try:
        if call.message:
            if call.data == 'yes':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                result = is_chat(call.from_user.id, call.message.chat.id)
                if result:
                    log({'cmd': 'clean',
                         'username': call.from_user.username,
                         'first_name': call.from_user.first_name})
                    db.clear()
                    State.last_add_user = None
                    bot.send_message(call.message.chat.id, 'table was cleaned')
            elif call.data == 'no':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, 'choose another command')
    except:
        traceback.print_exc()
        bot.send_message(call.message.chat.id, 'wrong request')


@bot.message_handler(commands=['delete'])
def delete(message):
    State.cur.user = message.from_user.id
    markup = types.InlineKeyboardMarkup()
    button_yes = types.InlineKeyboardButton(text='yes', callback_data='yes_del')
    button_no = types.InlineKeyboardButton(text='no', callback_data='no_del')
    markup.add(button_yes, button_no)
    bot.send_message(message.chat.id, text='are you sure you want to delete row?', reply_markup=markup)


@bot.callback_query_handler(lambda call: call.data in ['yes_del', 'no_del'] and filter(call))
def func_delete(call):
    try:
        if call.message:
            if call.data == 'yes_del':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                if State.last_add_user == call.from_user.id:
                    db.rm_last()
                    bot.send_message(call.message.chat.id, 'entry was deleted')
                    State.last_add_user = None
                    log({'cmd': 'delete',
                         'username': call.from_user.username,
                         'first_name': call.from_user.first_name})
                else:
                    bot.send_message(call.message.chat.id, 'you can\'t delete more entries')

            elif call.data == 'no_del':
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(call.message.chat.id, 'choose another command')
    except:
        traceback.print_exc()
        bot.send_message(call.message.chat.id, 'wrong request')


bot.polling()
