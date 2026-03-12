import express from "express";
import QRCode from "qrcode";

const app = express();
app.use(express.json());

const TOKEN = process.env.TOKEN;
const TELEGRAM = `https://api.telegram.org/bot${TOKEN}`;

// Храним шаги пользователя в памяти
const userState = new Map();

// Постоянные реквизиты
const PAYMENT = {
  name: "МЕЖРЕГИОНАЛЬНОЕ ОПЕРАЦИОННОЕ УФК (ФТС РОССИИ)",
  inn: "7730176610",
  kpp: "773001001",
  account: "03100643000000019502",
  bank: "Операционный департамент Банка России",
  bic: "024501901",
  corr: "40102810045370000002",
  kbk: "15301061301010000510",
  oktmo: "45328000",
  purpose: "Авансовые платежи в счет будущих таможенных и иных платежей",
  payerStatus: "16",
  paytReason: "00",
  taxPaytKind: "00",
  customsCode: "10700000"
};

async function sendMessage(chatId, text) {
  const response = await fetch(`${TELEGRAM}/sendMessage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      chat_id: chatId,
      text
    })
  });

  const data = await response.text();
  console.log("sendMessage:", data);
}

async function sendPhoto(chatId, photo, caption = "") {
  const response = await fetch(`${TELEGRAM}/sendPhoto`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      chat_id: chatId,
      photo,
      caption
    })
  });

  const data = await response.text();
  console.log("sendPhoto:", data);
}

function normalizeAmount(amountText) {
  const cleaned = amountText.replace(",", ".").replace(/\s+/g, "");
  const value = Number(cleaned);

  if (!Number.isFinite(value) || value <= 0) {
    return null;
  }

  return value;
}

function isValidInn(inn) {
  return /^\d{10}(\d{2})?$/.test(inn);
}

function buildQRString(payerInn, amountRub) {

  const sum = Math.round(amountRub * 100);

  return [
    "ST00012",
    "Name=МЕЖРЕГИОНАЛЬНОЕ ОПЕРАЦИОННОЕ УФК (ФТС РОССИИ)",
    "PersonalAcc=03100643000000019502",
    "BankName=Операционный департамент Банка России",
    "BIC=024501901",
    "CorrespAcc=40102810045370000002",
    "PayeeINN=7730176610",
    "KPP=773001001",
    "CBC=15301061301010000510",
    "OKTMO=45328000",
    "PaytReason=00",
    "DrawerStatus=16",
    "TaxPaytKind=00",
    "Purpose=Авансовые платежи в счет будущих таможенных и иных платежей",
    `PayerINN=${payerInn}`,
    `Sum=${sum}`
  ].join("|");
}

app.get("/", (req, res) => {
  res.status(200).send("Bot is running");
});

app.post("/", async (req, res) => {
  try {
    const message = req.body?.message;

    if (!message) {
      return res.sendStatus(200);
    }

    const chatId = message.chat?.id;
    const text = (message.text || "").trim();

    console.log("Incoming update:", JSON.stringify(req.body));
    console.log("Incoming text:", text);

    if (!chatId || !text) {
      return res.sendStatus(200);
    }

    if (!TOKEN) {
      console.error("TOKEN is missing");
      return res.sendStatus(200);
    }

    if (text === "/start" || text === "/new") {
      userState.set(chatId, { step: "wait_inn" });

      await sendMessage(
        chatId,
        "Здравствуйте! Для формирования QR-кода введите ИНН плательщика."
      );

      return res.sendStatus(200);
    }

    const state = userState.get(chatId);

    if (!state) {
      await sendMessage(
        chatId,
        "Нажмите /start, чтобы сформировать QR-код."
      );
      return res.sendStatus(200);
    }

    if (state.step === "wait_inn") {
      if (!isValidInn(text)) {
        await sendMessage(
          chatId,
          "ИНН должен содержать 10 или 12 цифр. Введите ИНН еще раз."
        );
        return res.sendStatus(200);
      }

      userState.set(chatId, {
        step: "wait_amount",
        payerInn: text
      });

      await sendMessage(
        chatId,
        "Введите сумму платежа в рублях. Например: 15000 или 15000,50"
      );

      return res.sendStatus(200);
    }

    if (state.step === "wait_amount") {
      const amount = normalizeAmount(text);

      if (!amount) {
        await sendMessage(
          chatId,
          "Не удалось распознать сумму. Введите сумму еще раз, например: 15000 или 15000,50"
        );
        return res.sendStatus(200);
      }

      const qrString = buildQRString(state.payerInn, amount);
      console.log("QR string:", qrString);

      const qrImage = await QRCode.toDataURL(qrString, {
        width: 900,
        margin: 2
      });

      await sendPhoto(
        chatId,
        qrImage,
        `QR-код сформирован\nИНН: ${state.payerInn}\nСумма: ${amount.toFixed(2)} ₽`
      );

      userState.set(chatId, { step: "wait_inn" });

      await sendMessage(
        chatId,
        "Готово. Для нового QR-кода введите следующий ИНН или нажмите /new"
      );

      return res.sendStatus(200);
    }

    await sendMessage(chatId, "Нажмите /start");
    return res.sendStatus(200);
  } catch (error) {
    console.error("Webhook error:", error);
    return res.sendStatus(200);
  }
});

const PORT = process.env.PORT || 8080;

app.listen(PORT, "0.0.0.0", () => {
  console.log("=== QR BOT V2 ===");
  console.log(`Listening on 0.0.0.0:${PORT}`);
});

