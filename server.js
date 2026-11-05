require('dotenv').config();
const express = require('express');
const crypto = require('crypto');
const path = require('path');

const app = express();
const SECRET = process.env.PAYSTACK_SECRET_KEY;
const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';
const PORT = process.env.PORT || 3000;

// Prices live on the server so buyers cannot change them. EDIT THESE (NGN per tonne).
const PRODUCTS = {
  'cassava-peels': { name: 'Cassava peels', pricePerTonne: 15000 },
  'rice-husks':    { name: 'Rice husks',    pricePerTonne: 12000 },
  'spent-grains':  { name: 'Spent grains',  pricePerTonne: 18000 },
  'crop-stalks':   { name: 'Crop stalks',   pricePerTonne: 10000 }
};

// Webhook needs the raw body to verify Paystack's signature, so it comes before express.json()
app.post('/api/paystack/webhook', express.raw({ type: '*/*' }), (req, res) => {
  const hash = crypto.createHmac('sha512', SECRET || '').update(req.body).digest('hex');
  if (hash !== req.headers['x-paystack-signature']) return res.sendStatus(401);
  const event = JSON.parse(req.body.toString());
  if (event.event === 'charge.success') {
    const d = event.data;
    // TODO: save to your database and mark the order paid (use d.reference, d.amount / 100)
    console.log('Paid:', d.reference, d.amount / 100, d.customer.email, d.metadata);
  }
  res.sendStatus(200);
});

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.post('/api/pay', async (req, res) => {
  try {
    const { name, email, phone, product, tonnes } = req.body;
    const item = PRODUCTS[product];
    const qty = Number(tonnes);
    if (!name || !/^\S+@\S+\.\S+$/.test(email || '') || !item || !(qty > 0)) {
      return res.status(400).json({ error: 'Check your name, email, product and quantity.' });
    }
    const amountNaira = item.pricePerTonne * qty;
    const r = await fetch('https://api.paystack.co/transaction/initialize', {
      method: 'POST',
      headers: { Authorization: `Bearer ${SECRET}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        amount: Math.round(amountNaira * 100), // kobo
        currency: 'NGN',
        callback_url: `${BASE_URL}/api/paystack/callback`,
        metadata: { name, phone, product: item.name, tonnes: qty }
      })
    });
    const data = await r.json();
    if (!data.status) return res.status(502).json({ error: data.message || 'Paystack error' });
    res.json({ url: data.data.authorization_url });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: 'Something went wrong. Try again.' });
  }
});

// Paystack sends the buyer back here; always verify server-side before confirming
app.get('/api/paystack/callback', async (req, res) => {
  const ref = req.query.reference;
  if (!ref) return res.redirect('/success.html?status=failed');
  try {
    const r = await fetch(`https://api.paystack.co/transaction/verify/${encodeURIComponent(ref)}`, {
      headers: { Authorization: `Bearer ${SECRET}` }
    });
    const data = await r.json();
    const ok = data.status && data.data.status === 'success';
    res.redirect(`/success.html?status=${ok ? 'success' : 'failed'}&ref=${encodeURIComponent(ref)}`);
  } catch {
    res.redirect('/success.html?status=failed');
  }
});

app.listen(PORT, () => console.log(`HeyCileng running on ${BASE_URL}`));
