import os, json, asyncio, traceback
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

BOT_TOKEN=os.getenv('BOT_TOKEN','').strip()
GOOGLE_CREDENTIALS_JSON=os.getenv('GOOGLE_CREDENTIALS_JSON','').strip()
GOOGLE_SHEET_ID=os.getenv('GOOGLE_SHEET_ID','').strip()
TIMEZONE=os.getenv('TIMEZONE','Asia/Vladivostok').strip()
DAILY_SEND_TIME=os.getenv('DAILY_SEND_TIME','10:30').strip()
REPORT_CHAT_ID=os.getenv('REPORT_CHAT_ID','').strip()
CLIENTS_PER_DAY=int(os.getenv('CLIENTS_PER_DAY','3'))
if not BOT_TOKEN: raise RuntimeError('Не задан BOT_TOKEN')
TZ=ZoneInfo(TIMEZONE)
bot=Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher(storage=MemoryStorage())

CONTACT_INTERVALS={'Регулярный':14,'Стабильный':21,'Нерегулярный':30}
RESULT_OPTIONS=[('new_request','🟢 Есть новый запрос'),('planned','📦 Планируются поставки'),('no_shipments','⏸ Пока поставок нет'),('season','📅 Пауза / сезонность'),('competitor','⚔️ Работает с другим подрядчиком'),('no_answer','📵 Не дозвонился'),('other','✏️ Другое')]

class Flow(StatesGroup):
    waiting_comment=State(); waiting_custom_date=State(); waiting_postpone_reason=State(); waiting_postpone_date=State()

def now_local(): return datetime.now(TZ)
def today_local(): return now_local().date()
def norm(v): return ' '.join(str(v or '').replace('\xa0',' ').split()).strip()
def parse_date(v):
    if v in (None,''): return None
    x=pd.to_datetime(str(v), errors='coerce', dayfirst=True)
    return None if pd.isna(x) else x.date()
def fmt_date(v):
    d=parse_date(v); return d.strftime('%d.%m.%Y') if d else '—'
def norm_category(v):
    t=norm(v).lower()
    if 'регуляр' in t: return 'Регулярный'
    if 'стабил' in t: return 'Стабильный'
    if 'нерегуляр' in t: return 'Нерегулярный'
    return norm(v) or 'Нерегулярный'

def spreadsheet():
    if not GOOGLE_CREDENTIALS_JSON or not GOOGLE_SHEET_ID: raise RuntimeError('Не заданы GOOGLE_CREDENTIALS_JSON или GOOGLE_SHEET_ID')
    info=json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
    return gspread.authorize(Credentials.from_service_account_info(info,scopes=scopes)).open_by_key(GOOGLE_SHEET_ID)

def ws(title, headers):
    ss=spreadsheet()
    try: sh=ss.worksheet(title)
    except gspread.WorksheetNotFound: sh=ss.add_worksheet(title=title,rows=2000,cols=max(10,len(headers)))
    if not sh.get_all_values(): sh.append_row(headers)
    return sh

CLIENT_HEADERS=['client','manager','category','last_order','last_request','last_contact','next_contact','last_result','active']
MANAGER_HEADERS=['manager','telegram_id','active']
TASK_HEADERS=['task_date','manager','telegram_id','client','status','created_at','completed_at']
COMM_HEADERS=['date','manager','telegram_id','client','result','comment','next_contact','source','created_at']

def clients_ws(): return ws('CLIENTS',CLIENT_HEADERS)
def managers_ws(): return ws('MANAGERS',MANAGER_HEADERS)
def tasks_ws(): return ws('TASKS',TASK_HEADERS)
def comm_ws(): return ws('COMMUNICATIONS',COMM_HEADERS)

def find_col(df, aliases):
    m={norm(c).lower():c for c in df.columns}
    for a in aliases:
        if norm(a).lower() in m: return m[norm(a).lower()]
    return None

def import_portfolio(path):
    df=pd.read_excel(path)
    cc=find_col(df,['Наименование','Клиент','Компания'])
    mc=find_col(df,['Опер. менеджер','Опер менеджер','Менеджер'])
    catc=find_col(df,['Категория','Тип клиента','Регулярность'])
    loc=find_col(df,['Последний заказ','Дата последнего заказа'])
    lrc=find_col(df,['Последний запрос','Дата последнего запроса'])
    missing=[n for n,c in [('Клиент',cc),('Менеджер',mc),('Категория',catc),('Последний заказ',loc),('Последний запрос',lrc)] if not c]
    if missing: raise ValueError('Не найдены колонки: '+', '.join(missing))
    sh=clients_ws(); rows=sh.get_all_records()
    idx={(norm(r.get('manager')).lower(),norm(r.get('client')).lower()):(i,r) for i,r in enumerate(rows,start=2)}
    new=upd=0
    for _,r in df.iterrows():
        client,manager=norm(r.get(cc)),norm(r.get(mc))
        if not client or not manager: continue
        key=(manager.lower(),client.lower())
        category=norm_category(r.get(catc)); last_order=fmt_date(r.get(loc)); last_request=fmt_date(r.get(lrc))
        if key in idx:
            n,old=idx[key]
            data=[client,manager,category,last_order,last_request,old.get('last_contact',''),old.get('next_contact',''),old.get('last_result',''),'1']
            sh.update(f'A{n}:I{n}',[data]); upd+=1
        else:
            sh.append_row([client,manager,category,last_order,last_request,'','','','1']); new+=1
    return new,upd

def register_manager(manager, tg):
    sh=managers_ws(); rows=sh.get_all_records()
    for i,r in enumerate(rows,start=2):
        if norm(r.get('manager')).lower()==manager.lower(): sh.update(f'A{i}:C{i}',[[manager,str(tg),'1']]); return
    sh.append_row([manager,str(tg),'1'])

def manager_by_tg(tg):
    for r in managers_ws().get_all_records():
        if str(r.get('telegram_id','')).strip()==str(tg) and str(r.get('active','1')).strip()!='0': return norm(r.get('manager'))
    return None

def active_managers():
    out=[]
    for r in managers_ws().get_all_records():
        if norm(r.get('manager')) and str(r.get('telegram_id','')).strip() and str(r.get('active','1')).strip()!='0': out.append((norm(r.get('manager')),int(r.get('telegram_id'))))
    return out

def client_state(client,manager):
    for r in clients_ws().get_all_records():
        if norm(r.get('client')).lower()==client.lower() and norm(r.get('manager')).lower()==manager.lower(): return r
    return {}

def update_client(client,manager,last_contact=None,next_contact=None,last_result=None):
    sh=clients_ws(); rows=sh.get_all_records()
    for i,r in enumerate(rows,start=2):
        if norm(r.get('client')).lower()==client.lower() and norm(r.get('manager')).lower()==manager.lower():
            data=[r.get('client',''),r.get('manager',''),r.get('category',''),r.get('last_order',''),r.get('last_request',''),last_contact if last_contact is not None else r.get('last_contact',''),next_contact if next_contact is not None else r.get('next_contact',''),last_result if last_result is not None else r.get('last_result',''),r.get('active','1')]
            sh.update(f'A{i}:I{i}',[data]); return

def mark_task(client,manager,status):
    sh=tasks_ws(); rows=sh.get_all_records(); ds=today_local().strftime('%Y-%m-%d')
    for i,r in enumerate(rows,start=2):
        if str(r.get('task_date','')).strip()==ds and norm(r.get('client')).lower()==client.lower() and norm(r.get('manager')).lower()==manager.lower():
            sh.update(f'E{i}:G{i}',[[status,r.get('created_at',''),now_local().strftime('%d.%m.%Y %H:%M')]]); return

def daily_tasks(manager,tg):
    ds=today_local().strftime('%Y-%m-%d'); tsh=tasks_ws(); rows=tsh.get_all_records()
    existing=[r for r in rows if str(r.get('task_date','')).strip()==ds and norm(r.get('manager')).lower()==manager.lower()]
    if existing: return existing
    today=today_local(); cand=[]
    for r in clients_ws().get_all_records():
        if str(r.get('active','1')).strip()=='0' or norm(r.get('manager')).lower()!=manager.lower(): continue
        client=norm(r.get('client')); category=norm_category(r.get('category')); interval=CONTACT_INTERVALS.get(category,30)
        nc=parse_date(r.get('next_contact')); lc=parse_date(r.get('last_contact')); lo=parse_date(r.get('last_order')); lr=parse_date(r.get('last_request'))
        if nc and nc>today: continue
        if nc: due=nc
        elif lc: due=lc+timedelta(days=interval)
        else: due=max([d for d in (lo,lr) if d], default=date(2000,1,1))+timedelta(days=interval)
        if due>today: continue
        cand.append(((today-due).days, {'client':client,'manager':manager,'status':'new'}))
    cand.sort(key=lambda x:(-x[0],x[1]['client'].lower()))
    selected=[x[1] for x in cand[:CLIENTS_PER_DAY]]
    created=now_local().strftime('%d.%m.%Y %H:%M')
    for t in selected: tsh.append_row([ds,manager,str(tg),t['client'],'new',created,''])
    return selected

def action_kb(client): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ Связался',callback_data=f'contact:{client}'),InlineKeyboardButton(text='⏰ Отложить',callback_data=f'postpone:{client}')]])
def result_kb(client): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=title,callback_data=f'result:{code}:{client}')] for code,title in RESULT_OPTIONS])
def next_kb(client): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='7 дней',callback_data=f'next:7:{client}'),InlineKeyboardButton(text='14 дней',callback_data=f'next:14:{client}')],[InlineKeyboardButton(text='30 дней',callback_data=f'next:30:{client}'),InlineKeyboardButton(text='60 дней',callback_data=f'next:60:{client}')],[InlineKeyboardButton(text='📅 Выбрать дату',callback_data=f'next_custom:{client}')]])
def postpone_kb(client): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='7 дней',callback_data=f'postpone_days:7:{client}'),InlineKeyboardButton(text='14 дней',callback_data=f'postpone_days:14:{client}')],[InlineKeyboardButton(text='30 дней',callback_data=f'postpone_days:30:{client}'),InlineKeyboardButton(text='60 дней',callback_data=f'postpone_days:60:{client}')],[InlineKeyboardButton(text='📅 Выбрать дату',callback_data=f'postpone_custom:{client}')]])

async def send_today(tg,manager):
    tasks=[t for t in daily_tasks(manager,tg) if str(t.get('status','')) not in {'done','postponed'}]
    if not tasks: await bot.send_message(tg,'✅ На сегодня клиентов для обязательного контакта нет.'); return
    await bot.send_message(tg,f'📋 <b>Клиенты на сегодня</b>\nМенеджер: <b>{manager}</b>\nКоличество: <b>{len(tasks)}</b>')
    for i,t in enumerate(tasks,start=1):
        c=norm(t.get('client')); s=client_state(c,manager)
        text=f'<b>{i}. {c}</b>\nКатегория: {norm_category(s.get("category"))}\nПоследний контакт: <b>{fmt_date(s.get("last_contact"))}</b>\nПоследний заказ: {fmt_date(s.get("last_order"))}\nПоследний запрос: {fmt_date(s.get("last_request"))}'
        await bot.send_message(tg,text,reply_markup=action_kb(c))

@dp.message(CommandStart())
async def start(m:Message): await m.answer('👋 <b>Контроль коммуникации</b>\n\n/register ФИО — привязать менеджера\n/today — получить 3 клиента на сегодня\n/status — проверить привязку\n\nРуководитель может загрузить Excel-портфель прямо сюда.')

@dp.message(Command('register'))
async def register(m:Message):
    manager=m.text.replace('/register','',1).strip()
    if not manager: await m.answer('Например: <code>/register Лилия Буглак</code>'); return
    register_manager(manager,m.from_user.id); await m.answer(f'✅ Привязано: <b>{manager}</b>')

@dp.message(Command('status'))
async def status(m:Message):
    manager=manager_by_tg(m.from_user.id); await m.answer(f'✅ Вы зарегистрированы как <b>{manager}</b>' if manager else '⚠️ Сначала /register ФИО')

@dp.message(Command('today'))
async def today_cmd(m:Message):
    manager=manager_by_tg(m.from_user.id)
    if not manager: await m.answer('Сначала /register ФИО'); return
    await send_today(m.from_user.id,manager)

@dp.message(F.document)
async def upload(m:Message):
    fn=m.document.file_name or 'portfolio.xlsx'
    if not fn.lower().endswith(('.xlsx','.xls')): await m.answer('Нужен Excel .xlsx/.xls'); return
    path=Path('/tmp')/Path(fn).name
    try:
        await bot.download(m.document,destination=path); new,upd=import_portfolio(str(path)); await m.answer(f'✅ Портфель обновлен\nНовых: <b>{new}</b>\nОбновлено: <b>{upd}</b>')
    except Exception as e:
        traceback.print_exc(); await m.answer(f'❌ Ошибка: <code>{e}</code>')

@dp.callback_query(F.data.startswith('contact:'))
async def contact(c:CallbackQuery):
    client=c.data.split(':',1)[1]; await c.message.answer(f'Какой результат по <b>{client}</b>?',reply_markup=result_kb(client)); await c.answer()

@dp.callback_query(F.data.startswith('result:'))
async def result(c:CallbackQuery,state:FSMContext):
    _,code,client=c.data.split(':',2); manager=manager_by_tg(c.from_user.id); title=dict(RESULT_OPTIONS).get(code,'Другое')
    await state.update_data(client=client,manager=manager,result=title); await state.set_state(Flow.waiting_comment); await c.message.answer('Напиши короткий комментарий. Если не нужен — отправь <code>-</code>.'); await c.answer()

@dp.message(Flow.waiting_comment)
async def comment(m:Message,state:FSMContext):
    d=await state.get_data(); await state.update_data(comment='' if m.text.strip()=='-' else m.text.strip()); await m.answer('Когда вернуться к клиенту?',reply_markup=next_kb(d['client']))

@dp.callback_query(F.data.startswith('next:'))
async def next_days(c:CallbackQuery,state:FSMContext):
    _,days,client=c.data.split(':',2); d=await state.get_data(); nd=today_local()+timedelta(days=int(days)); result=d['result']; comment=d.get('comment','')
    comm_ws().append_row([today_local().strftime('%d.%m.%Y'),d['manager'],str(c.from_user.id),client,result,comment,nd.strftime('%d.%m.%Y'),'telegram',now_local().strftime('%d.%m.%Y %H:%M')])
    update_client(client,d['manager'],today_local().strftime('%d.%m.%Y'),nd.strftime('%d.%m.%Y'),result); mark_task(client,d['manager'],'done'); await state.clear(); await c.message.answer(f'✅ Зафиксировано. Следующий контакт: <b>{nd.strftime("%d.%m.%Y")}</b>'); await c.answer()

@dp.callback_query(F.data.startswith('next_custom:'))
async def next_custom(c:CallbackQuery,state:FSMContext):
    client=c.data.split(':',1)[1]; await state.update_data(client=client); await state.set_state(Flow.waiting_custom_date); await c.message.answer('Дата в формате <code>25.08.2026</code>'); await c.answer()

@dp.message(Flow.waiting_custom_date)
async def custom_date(m:Message,state:FSMContext):
    try: nd=datetime.strptime(m.text.strip(),'%d.%m.%Y').date()
    except ValueError: await m.answer('Формат ДД.ММ.ГГГГ'); return
    d=await state.get_data(); client=d['client']; result=d['result']; comment=d.get('comment',''); comm_ws().append_row([today_local().strftime('%d.%m.%Y'),d['manager'],str(m.from_user.id),client,result,comment,nd.strftime('%d.%m.%Y'),'telegram',now_local().strftime('%d.%m.%Y %H:%M')]); update_client(client,d['manager'],today_local().strftime('%d.%m.%Y'),nd.strftime('%d.%m.%Y'),result); mark_task(client,d['manager'],'done'); await state.clear(); await m.answer(f'✅ Зафиксировано. Следующий контакт: <b>{nd.strftime("%d.%m.%Y")}</b>')

@dp.callback_query(F.data.startswith('postpone:'))
async def postpone(c:CallbackQuery,state:FSMContext):
    client=c.data.split(':',1)[1]; manager=manager_by_tg(c.from_user.id); await state.update_data(client=client,manager=manager); await state.set_state(Flow.waiting_postpone_reason); await c.message.answer(f'Почему откладываем <b>{client}</b>?'); await c.answer()

@dp.message(Flow.waiting_postpone_reason)
async def postpone_reason(m:Message,state:FSMContext):
    await state.update_data(reason=m.text.strip()); d=await state.get_data(); await m.answer('На какой срок?',reply_markup=postpone_kb(d['client']))

@dp.callback_query(F.data.startswith('postpone_days:'))
async def postpone_days(c:CallbackQuery,state:FSMContext):
    _,days,client=c.data.split(':',2); d=await state.get_data(); nd=today_local()+timedelta(days=int(days)); reason=d.get('reason',''); update_client(client,d['manager'],next_contact=nd.strftime('%d.%m.%Y'),last_result='Отложено: '+reason); mark_task(client,d['manager'],'postponed'); comm_ws().append_row([today_local().strftime('%d.%m.%Y'),d['manager'],str(c.from_user.id),client,'Отложено',reason,nd.strftime('%d.%m.%Y'),'telegram',now_local().strftime('%d.%m.%Y %H:%M')]); await state.clear(); await c.message.answer(f'⏰ Отложено до <b>{nd.strftime("%d.%m.%Y")}</b>'); await c.answer()

async def daily_loop():
    last=None
    while True:
        try:
            now=now_local()
            if now.strftime('%H:%M')==DAILY_SEND_TIME and last!=now.date():
                for manager,tg in active_managers():
                    try: await send_today(tg,manager)
                    except Exception: traceback.print_exc()
                last=now.date()
        except Exception: traceback.print_exc()
        await asyncio.sleep(30)

async def main():
    print('CLIENT COMMUNICATION BOT STARTING',flush=True)
    asyncio.create_task(daily_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__=='__main__': asyncio.run(main())

