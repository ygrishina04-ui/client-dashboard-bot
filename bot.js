import express from "express";

const app = express();
app.use(express.json({ limit: "1mb" }));

app.get("/", (req, res) => {
  res.status(200).send("Bot is running");
});

app.post("/", (req, res) => {
  try {
    console.log("Webhook received");
    console.log(JSON.stringify(req.body));
    return res.sendStatus(200);
  } catch (error) {
    console.error("POST error:", error);
    return res.sendStatus(200);
  }
});

const PORT = Number(process.env.PORT) || 3000;

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Listening on 0.0.0.0:${PORT}`);
});
