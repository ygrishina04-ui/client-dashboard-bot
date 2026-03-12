import express from "express";

const app = express();
app.use(express.json());

const TOKEN = process.env.TOKEN;

app.get("/", (req, res) => {
  res.status(200).send("Bot is running");
});

app.post("/", async (req, res) => {
  try {
    console.log("Webhook body:", JSON.stringify(req.body));

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
      if (!TOKEN) {
        console.error("TOKEN is missing");
        return res.sendStatus(200);
      }

      const response = await fetch(`https://api.telegram.org/bot${TOKEN}/sendMessage`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          chat_id: chatId,
          text: "Бот работает 🚀"
        })
      });

      const data = await response.text();
      console.log("Telegram response:", data);
    }

    return res.sendStatus(200);
  } catch (error) {
    console.error("POST / crashed:", error);
    return res.sendStatus(200);
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, "0.0.0.0", () => {
  console.log(`Server started on port ${PORT}`);
});
