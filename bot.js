import express from "express";
import QRCode from "qrcode";

const app = express();
app.use(express.json({ limit: "10mb" }));

const TOKEN = process.env.TOKEN;
const PORT = process.env.PORT || 8080;

if (!TOKEN) {
  console.error("TOKEN is missing in environment variables");
}

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

async function telegramRequest(method, payload, isFormData = false) {
  try {
    const response = await fetch(`${TELEGRAM}/${method}`, {
      method: "POST",
      headers: isFormData
        ? undefined
        : { "Content-Type": "application/json" },
      body: isFormData ? payload : JSON.stringify(payload)
    });

    const text = await response.text();
    console.log(`${method}:`, text);

    return text;
  } catch (error) {
    console.error(`Error in ${method}:`, error);
    throw error;
  }
}

async function sendMessage(chatId, text) {
  return telegramRequest("sendMessage", {
    chat_id: chatId,
    text
  });
}

async function sendPhoto(chatId, qrBuffer, caption = "") {
  const formData = new FormData();
  formData.append("chat_id", String(chatId));
  formData.append("caption", caption);
  formData.append(
    "photo",
    new Blob([qrBuffer], { type: "image/png" }),
    "qr.png"
  );

  return telegramRequest("sendPhoto", formData, true);
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
  return /^\d{10}(\d{12})?$/.test(inn) || /^\d{10}$|^\d{12}$/.test(inn);
}

function buildQRString(payerInn, amountRub) {
  const sum = Math.round(amountRub * 100);

  return [
    "ST00012",
    `Name=${PAYMENT.name}`,
    `PersonalAcc=${PAYMENT.account}`,
    `BankName=${PAYMENT.bank}`,
    `BIC=${PAYMENT.bic}`,
    `CorrespAcc=${PAYMENT.corr}`,
    `PayeeINN=${PAYMENT.inn}`,
    `KPP=${PAYMENT.kpp}`,
    `CBC=${PAYMENT.kbk}`,
    `OKTMO=${PAYMENT.oktmo}`,
    `PaytReason=${PAYMENT.paytReason}`,
    `DrawerStatus=${PAYMENT.payerStatus}`,
    `TaxPaytKind=${PAYMENT.taxPaytKind}`,
    `Purpose=${PAYMENT.purpose}`,
    `PayerINN=${payerInn}`,
    `Sum=${sum}`
  ].join("|");
}

// Проверка, что сервер жив
app.get("/", (req, res) => {
  res.status(200).send("Bot is running");
});

// Для ручной проверки webhook
app.get("/health", (req, res) => {
  res.status(200).json({
    ok: true,
    tokenExists: !!TOKEN,
    port: PORT
  });
});

app.post("/", async (req, res) => {
  // Telegram должен быстро получить 200
  res.sendStatus(200);

  try {
    console.log("Incoming update:", JSON.stringify(req.body, null, 2));

    const message = req.body?.message;

    if (!message) {
      console.log("No message in update");
      return;
    }

    const chatId = message.chat?.id;
    const text = (message.text || "").trim();

    console.log("chatId:", chatId);
    console.log("text:", text);

    if (!TOKEN) {
      console.error("TOKEN is missing");
      return;
    }

    if (!chatId) {
      console.error("No chatId");
      return;
    }

    if (!text) {
      await sendMessage(chatId, "Пожалуйста, отправьте текстовое сообщение.");
      return;
    }

    if (text === "/start" || text === "/new") {
      userState.set(chatId, { step: "wait_inn" });

      await sendMessage(
        chatId,
        "Здравствуйте! Для формирования QR-кода введите ИНН плательщика."
      );
      return;
    }

    const state = userState.get(chatId);

    if (!state) {
      await sendMessage(chatId, "Нажмите /start, чтобы сформировать QR-код.");
      return;
    }

    if (state.step === "wait_inn") {
      if (!isValidInn(text)) {
        await sendMessage(
          chatId,
          "ИНН должен содержать 10 или 12 цифр. Введите ИНН еще раз."
        );
        return;
      }

      userState.set(chatId, {
        step: "wait_amount",
        payerInn: text
      });

      await sendMessage(
        chatId,
        "Введите сумму платежа в рублях. Например: 15000 или 15000,50"
      );
      return;
    }

    if (state.step === "wait_amount") {
      const amount = normalizeAmount(text);

      if (!amount) {
        await sendMessage(
          chatId,
          "Не удалось распознать сумму. Введите сумму еще раз, например: 15000 или 15000,50"
        );
        return;
      }

      const qrString = buildQRString(state.payerInn, amount);
      console.log("QR string:", qrString);

      const qrBuffer = await QRCode.toBuffer(qrString, {
        type: "png",
        width: 900,
        margin: 2
      });

      await sendPhoto(
        chatId,
        qrBuffer,
        `QR-код сформирован\nИНН: ${state.payerInn}\nСумма: ${amount.toFixed(2)} ₽`
      );

      userState.set(chatId, { step: "wait_inn" });

      await sendMessage(
        chatId,
        "Готово. Для нового QR-кода введите следующий ИНН или нажмите /new"
      );
      return;
    }

    await sendMessage(chatId, "Нажмите /start");
  } catch (error) {
    console.error("Webhook error:", error);
  }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log("=== QR BOT ===");
  console.log(`Listening on 0.0.0.0:${PORT}`);
});
