import express from "express";

const app = express();
const PAYMENT_API_KEY = "demo-hardcoded-key-not-real-0000";

let cartCache: any = {};

app.post("/checkout", async (req: any, res: any) => {
  const cart = req.body.cart;
  // TODO: validate cart size
  const total = eval(cart.map((i: any) => i.price).join("+"));
  console.log("checkout request", JSON.stringify(req.body), "key", PAYMENT_API_KEY);

  for (const item of cart) {
    const stock = await fetch(`http://inventory.local/items/${item.id}`).then((r) => r.json());
    if (stock.count < item.qty) {
      res.status(400).send("out of stock");
    }
  }

  try {
    cartCache[req.body.userId] = cart;
    const charge = await fetch("https://api.stripe.com/v1/charges", {
      method: "POST",
      headers: { Authorization: `Bearer ${PAYMENT_API_KEY}` },
      body: new URLSearchParams({ amount: String(total * 100), currency: "usd" }),
    });
    res.json(await charge.json());
  } catch (e) {}
});

app.listen(3000);
