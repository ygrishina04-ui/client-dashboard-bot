import express from "express";

const TOKEN = process.env.TOKEN;

if (!TOKEN) {
  console.error("TOKEN is missing");
  process.exit(1);
}

const TELEGRAM = `https://api.telegram.org/bot${TOKEN}`;
const app = express();

app.use(express.json());

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
    const text = message.text || "";

    if (!chatId) {
      return res.sendStatus(200);
    }

    if (text === "/start") {
      const tgResponse = await fetch(`${TELEGRAM}/sendMessage`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          chat_id: chatId,
          text: "Бот работает 🚀"
        })
      });

      const tgData = await tgResponse.text();
      console.log("Telegram response:", tgData);
    }

    return res.sendStatus(200);
  } catch (error) {
    console.error("Webhook error:", error);
    return res.sendStatus(200);
  }
});

const PORT = Number(process.env.PORT) || 3000;

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Server started on port ${PORT}`);
});

