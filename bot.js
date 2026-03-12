import express from "express";
import QRCode from "qrcode";

const app = express();
app.use(express.json());

const TOKEN = process.env.TOKEN;
const TELEGRAM = `https://api.telegram.org/bot${TOKEN}`;

// постоянные реквизиты
const PAYMENT = {
  name: "МЕЖРЕГИОНАЛЬНОЕ ОПЕРАЦИОННОЕ УФК (ФТС РОССИИ)",
  inn: "7730176610",
  kpp: "773001001",
  account: "03100643000000019502",
  bank: "Операционный департамент Банка России",
  bic: "024501901",
  corr: "40102810045370000002",
  purpose:
    "Авансовые платежи в счет будущих таможенных и иных платежей КБК 15301061301010000510 ОКТМО 45328000",
};

async function sendMessage(chatId, text) {
  await fetch(`${TELEGRAM}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
    }),
  });
}

async function sendPhoto(chatId, photo) {
  await fetch(`${TELEGRAM}/sendPhoto`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      photo,
      caption: "QR код для оплаты",
    }),
  });
}

function buildQR(innPayer, amount) {
  const sum = Math.round(amount * 100); // копейки

  return `ST00012|
Name=${PAYMENT.name}|
PersonalAcc=${PAYMENT.account}|
BankName=${PAYMENT.bank}|
BIC=${PAYMENT.bic}|
CorrespAcc=${PAYMENT.corr}|
PayeeINN=${PAYMENT.inn}|
KPP=${PAYMENT.kpp}|
Purpose=${PAYMENT.purpose}|
PayerINN=${innPayer}|
Sum=${sum}`.replace(/\n/g, "");
}

app.get("/", (req, res) => {
  res.send("Bot is running");
});

app.post("/", async (req, res) => {
  try {
    const message = req.body?.message;
    if (!message) return res.sendStatus(200);

    const chatId = message.chat.id;
    const text = (message.text || "").trim();

    if (text === "/start") {
      await sendMessage(
        chatId,
        `Отправьте данные так:

ИНН сумма

Пример:
7701234567 15000`
      );
      return res.sendStatus(200);
    }

    const parts = text.split(" ");

    if (parts.length === 2) {
      const inn = parts[0];
      const sum = parseFloat(parts[1]);

      const qrString = buildQR(inn, sum);

      const qrImage = await QRCode.toDataURL(qrString, {
        width: 800,
        margin: 2,
      });

      await sendPhoto(chatId, qrImage);
    }

    return res.sendStatus(200);
  } catch (e) {
    console.error(e);
    return res.sendStatus(200);
  }
});

const PORT = process.env.PORT || 8080;

app.listen(PORT, "0.0.0.0", () => {
  console.log("Server started", PORT);
});
